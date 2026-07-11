"""
MetaOptimizer — 元参数优化器

从 Impact Tracker 读取模块效果指标，自动调整下次 pipeline 的参数。

架构定位:
  context_assemble 调用 engine.run() 之前运行，
  输出 override 参数（prefetch_ids, isrel_threshold_override, ltm_profile 等）
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("galaxyos.metaopt")


class MetaOptimizer:
    """
    元参数优化器。

    用法:
      opt = MetaOptimizer()
      overrides = opt.compute(session_id, impact_tracker)
      # → { "isrel_threshold_override": ..., "prefetch_ids": ..., ... }
    """

    # ─── SSM 参数上下界 ────
    SSM_K_MIN = 1
    SSM_K_MAX = 10

    # ─── IsREL 参数上下界 ────
    ISREL_THRESH_MIN = 0.05
    ISREL_THRESH_DEFAULT = 0.5
    ISREL_THRESH_MAX = 0.95

    # ─── MemoryOS 参数 ────
    MEMORYOS_PROFILE_MIN = 100
    MEMORYOS_PROFILE_MAX = 2000
    MEMORYOS_PROFILE_DEFAULT = 1000

    def __init__(self):
        # session_id → 当前的参数字典
        self._current: Dict[str, Dict[str, Any]] = {}

    def compute(
        self,
        session_id: str,
        impact: "ImpactTracker",
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        根据 Impact Tracker 的指标计算本轮参数覆盖。

        Args:
            session_id: 会话ID
            impact: ImpactTracker 实例
            ctx: 上一轮的完整上下文

        Returns:
            {param_name: value} — 传递给下轮 pipeline 的覆盖参数
        """
        params = self._current.get(session_id, self._defaults())

        # ── SSM 窗口自适应 ────
        ssm_hit = impact.get_hit_rate(session_id, "ssm")
        ssm_trend = impact.get_trend(session_id, "ssm")

        if ssm_hit < 0.2:
            # 预测几乎全错 → 缩小窗口，保守预测
            params["ssm_k"] = max(1, params.get("ssm_k", 3) - 1)
            logger.info(f"[metaopt] SSM hit={ssm_hit:.2f} < 0.2 → K={params['ssm_k']}")
        elif ssm_hit > 0.7 and ssm_trend == "up":
            # 预测很准 → 可以扩大窗口
            params["ssm_k"] = min(10, params.get("ssm_k", 3) + 1)
            logger.info(f"[metaopt] SSM hit={ssm_hit:.2f} > 0.7 → K={params['ssm_k']}")
        # else 维持不变

        # ── IsREL 阈值自适应 ────
        isrel_metrics = impact.get_metrics_snapshot(session_id).get("isrel", {})
        if isrel_metrics:
            quality_delta = isrel_metrics.get("quality_delta", 0)
            if isrel_metrics.get("threshold_override") is not None:
                if quality_delta < -0.1:
                    # 降阈后质量反而下降 → 恢复默认
                    params["isrel_threshold_override"] = None
                    logger.info(f"[metaopt] IsREL quality_delta={quality_delta:.2f} < -0.1 → 恢复默认阈值")
                elif quality_delta > 0.1:
                    # 降阈有效果 → 记住这个策略
                    logger.info(f"[metaopt] IsREL quality_delta={quality_delta:.2f} > 0.1 → 策略有效")

        # ── MemoryOS profile 长度自适应 ────
        mem_metrics = impact.get_metrics_snapshot(session_id).get("memoryos", {})
        if mem_metrics:
            usage = mem_metrics.get("usage_rate", 0.5)
            if usage < 0.2:
                # 利用率低 → 缩短 profile
                current_len = mem_metrics.get("profile_length", 1000)
                new_len = max(100, current_len - 200)
                # 记录在 params 里，让下层决定截断
                params["memoryos_max_len"] = new_len
                logger.info(f"[metaopt] MemoryOS usage={usage:.2f} < 0.2 → 缩短至 {new_len}")
            elif usage > 0.8:
                # 利用率高 → 可以给更多空间
                current_len = mem_metrics.get("profile_length", 1000)
                new_len = min(2000, current_len + 200)
                params["memoryos_max_len"] = new_len
                logger.info(f"[metaopt] MemoryOS usage={usage:.2f} > 0.8 → 扩展至 {new_len}")

        # ── CoEvolve 自适应 ────
        coe_metrics = impact.get_metrics_snapshot(session_id).get("coevolve", {})
        if coe_metrics and coe_metrics.get("failure"):
            # 检查上次降阈后检索是否恢复
            crag_trend = impact.get_trend(session_id, "crag")
            if crag_trend == "down":
                # 检索一直差 → 可能需要别的策略而非单纯降阈
                logger.info("[metaopt] CoEvolve 检测失败但检索趋势向下 → 追加 multi_search")
                params["coevolve_strategy"] = "multi_search"
            else:
                params["coevolve_strategy"] = "continue"

        # 保存
        self._current[session_id] = params
        return self._to_overrides(params)

    def track_ssm_actual(
        self,
        session_id: str,
        predicted_ids: List[str],
        heat_top: List[Any],
    ) -> Dict[str, Any]:
        """SSM 预测 vs 实际命中（供 MetaOptimizer 内部使用）"""
        actual_ids = set()
        for h in heat_top:
            if isinstance(h, dict):
                actual_ids.add(h.get("id", ""))
            else:
                actual_ids.add(str(h))

        predicted_set = set(predicted_ids)
        if predicted_set:
            hits = predicted_set & actual_ids
            return {"hit_rate": len(hits) / len(predicted_set), "hits": len(hits)}
        return {"hit_rate": 0.0, "hits": 0}

    # ─── 内部 ───────────────────────────────────────────

    def _defaults(self) -> Dict[str, Any]:
        return {
            "ssm_k": 3,
            "isrel_threshold_override": None,
            "memoryos_max_len": 1000,
            "coevolve_strategy": "default",
        }

    def _to_overrides(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """内部参数字典 → 对外暴露的 override 参数"""
        overrides = {}
        if params.get("isrel_threshold_override") is not None:
            overrides["isrel_threshold_override"] = params["isrel_threshold_override"]
        if params.get("ssm_k") is not None:
            overrides["ssm_k"] = params["ssm_k"]
        if params.get("memoryos_max_len") is not None:
            overrides["memoryos_max_len"] = params["memoryos_max_len"]
        if params.get("coevolve_strategy") is not None:
            overrides["coevolve_strategy"] = params["coevolve_strategy"]
        return overrides
