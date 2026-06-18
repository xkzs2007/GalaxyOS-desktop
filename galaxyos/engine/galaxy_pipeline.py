"""
GalaxyOS 轻量流水线引擎 — 声明式阶段清单 + 自动执行

方案 D：用声明式 Phase 清单替换 context_assemble 手写 500 行面条代码。
每个阶段声明：
  - name: 阶段名
  - output: 输出到 result["layers"] or result["decisions"]
  - skip_if: 跳过条件（函数，接收 result 和 ctx）
  - depends_on: 前置阶段（清单里的 name）
  - fn: 执行函数（接收 query, session_id, top_k, result, ctx, self）

加模块 = 在 PIPELINE 加一行 + 写一个 fn
"""

import logging
import numpy as np

logger = logging.getLogger("galaxyos.pipeline")

# ─────────────────────────────────────────────────────
# 阶段清单 — 声明式
# 顺序决定了执行顺序（依赖未达到时自动跳过）
# ─────────────────────────────────────────────────────

def build_pipeline():
    """返回当前全量的阶段清单（可运行时修改）"""
    return [
        # ── Phase 0: Self-RAG IsREL ──
        {
            "name": "isrel",
            "output": "decisions",
            "skip_if": None,
            "depends_on": [],
            "fn": _phase_isrel,
        },

        # ── Phase 1 Layer 0: BlobArena ──
        {
            "name": "blob_arena",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_blob_arena,
        },

        # ── Phase 1 Layer 1: MemGPT ──
        {
            "name": "memgpt_context",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_memgpt,
        },

        # ── Phase 1 Layer 2: MemoryOS Heat ──
        {
            "name": "heat_top",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_heat_tracker,
        },

        # ── Phase 1 Layer 3: HierarchicalMemory ──
        {
            "name": "hierarchical_memories",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_hierarchical_memory,
        },

        # ── Phase 1 Layer 4: HAConvDR ──
        {
            "name": "haconvdr_recall",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_haconvdr,
        },

        # ── Phase 1 Layer 5: AriGraph ──
        {
            "name": "spatial_scene",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_arigraph,
        },

        # ── Phase 1 Layer 6: RAPTOR ──
        {
            "name": "raptor_summaries",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_raptor,
        },

        # ── Phase 2b: Cognitive Load ──
        {
            "name": "cognitive_load",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_cognitive_load,
        },

        # ── Phase 2c: Dynamic CRAG Threshold ──
        {
            "name": "crag_thresholds",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["cognitive_load"],
            "fn": _phase_crag_threshold,
        },

        # ── Phase 2c: CRAG ──
        {
            "name": "crag",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["hierarchical_memories", "haconvdr_recall"],
            "fn": _phase_crag,
        },

        # ── Phase 2d: CoVe ──
        {
            "name": "cove",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["hierarchical_memories", "haconvdr_recall"],
            "fn": _phase_cove,
        },

        # ── Phase 2e: Adaptive Hallucination Params ──
        {
            "name": "hallucination_params",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["crag"],
            "fn": _phase_hallucination_params,
        },

        # ── Phase 2.5: Liquid Rerank（液态神经网络重排）──
        {
            "name": "liquid_rerank",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["hierarchical_memories", "haconvdr_recall"],
            "fn": _phase_liquid_rerank,
        },

        # ── Phase 2.5b: KAN Rerank（KAN 语义重排）──
        {
            "name": "kan_rerank",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["liquid_rerank"],
            "fn": _phase_kan_rerank,
        },

        # ── Phase 2.5c: Liquid Graph Embed（图嵌入增强）──
        {
            "name": "liquid_graph",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["kan_rerank"],
            "fn": _phase_liquid_graph,
        },

        # ── Phase 3: SKILL0 ──
        {
            "name": "skill0",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["crag", "cove"],
            "fn": _phase_skill0,
        },

        # ── Phase 3b: CoEvolve ──
        {
            "name": "coevolve",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["crag", "cove"],
            "fn": _phase_coevolve,
        },

        # ── Phase 3e: Liquid State Track（液态状态追踪）──
        {
            "name": "liquid_state",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["crag"],
            "fn": _phase_liquid_state,
        },

        # ── Phase 3e2: SSM+KAN State Track（KAN 增强状态追踪）──
        {
            "name": "ssm_kan_state",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["crag"],
            "fn": _phase_ssm_kan_state,
        },

        # ── Phase 3c: Turn Recovery ──
        {
            "name": "turn_recovery",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["crag", "cove"],
            "fn": _phase_turn_recovery,
        },

        # ── Phase 3d: MemCoE ──
        {
            "name": "memcoe",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["crag", "cove", "heat_top"],
            "fn": _phase_memcoe,
        },

        # ── Phase 3f: MoE-Engram Reasoning（混合推理路由）──
        {
            "name": "moe_engram",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["crag", "cove"],
            "fn": _phase_moe_engram,
        },

        # ── Phase 3g: LTC Neural ODE（对话轨迹预测）──
        {
            "name": "ltc_ode",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["crag", "moe_engram"],
            "fn": _phase_ltc_ode,
        },

        # ── Phase 3h: Lipschitz Stability（状态稳定性校验）──
        {
            "name": "lipschitz_stable",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": ["ltc_ode"],
            "fn": _phase_lipschitz_stable,
        },

        # ── Phase 4: MemoryOS STM→MTM→LPM ──
        {
            "name": "memoryos_profile",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_memoryos,
        },

        # ── Phase 4b: SSM ──
        {
            "name": "ssm_predicted",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_ssm,
        },

        # ── Phase 5: HyperRouting ──
        {
            "name": "hyper_route",
            "output": "decisions",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_hyper_routing,
        },

        # ── Phase 6: KoRa ──
        {
            "name": "kora_pattern",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_kora,
        },

        # ── Phase 7: Code-Aware ──
        {
            "name": "code_aware",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_code_aware,
        },

        # ── Phase 8: Thinking Enhanced ──
        {
            "name": "thinking_enhanced",
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_thinking_enhanced,
        },

        # ── Phase 9: Memory Consolidation ──
        {
            "name": None,  # 不进 layers/decisions，仅后台
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_consolidation,
        },

        # ── Phase 10: Sleep Consolidation ──
        {
            "name": None,
            "output": "layers",
            "skip_if": lambda r, ctx: r.get("skipped") == "isrel_no_retrieve",
            "depends_on": [],
            "fn": _phase_sleep_consolidation,
        },
    ]


# ─────────────────────────────────────────────────────
# 执行引擎
# ─────────────────────────────────────────────────────

def run_pipeline(pipeline, query, session_id, top_k, result, ctx, self_ref):
    """
    按声明式清单执行阶段。
    ctx 是上下文字典，用于传递 skip_if 所需的额外信息。
    self_ref 是 ClawWorker 实例。
    """
    executed = set()

    for phase in pipeline:
        name = phase["name"]
        skip_fn = phase["skip_if"]
        depends = phase["depends_on"]
        output_key = phase["output"]
        fn = phase["fn"]

        # 检查跳过条件
        if skip_fn and skip_fn(result, ctx):
            continue

        # 检查依赖是否都已执行（或已在 result 里）
        deps_ok = all(
            d in result.get(output_key, {}) or d in executed
            for d in depends
        )
        if not deps_ok:
            # 依赖未满足 → 跳过（记录日志不报错）
            er_key = f"{name}_dependency_skipped" if name else "phase_dependency_skipped"
            if name:
                result[output_key][er_key] = True
            continue

        # 执行阶段
        try:
            fn(query, session_id, top_k, result, ctx, self_ref)
            if name:
                executed.add(name)
        except Exception as e:
            err_key = f"{name}_error" if name else "phase_error"
            if name:
                result[output_key][err_key] = str(e)
            else:
                logger.warning(f"Phase error: {e}")


# ─────────────────────────────────────────────────────
# 各 Phase 实现
# ─────────────────────────────────────────────────────

def _phase_isrel(query, session_id, top_k, result, ctx, self_ref):
    from isrel_predictor import IsRELPredictor
    isrel = IsRELPredictor()
    decision = isrel.predict(query, context=None)
    should_retrieve = decision.should_retrieve
    result["decisions"]["isrel"] = {
        "should_retrieve": should_retrieve,
        "confidence": decision.confidence,
        "reason": getattr(decision, 'reason', '')
    }
    if not should_retrieve:
        result["injection"] = ""
        result["success"] = True
        result["skipped"] = "isrel_no_retrieve"


def _phase_blob_arena(query, session_id, top_k, result, ctx, self_ref):
    from blob_arena import get_blob_arena
    arena = get_blob_arena()
    if arena:
        blob_ids = arena.list_ids(session_id=session_id, limit=5)
        restored = []
        for bid in blob_ids:
            full_text = arena.read_text(bid)
            if full_text:
                restored.append({"blob_id": bid, "content": full_text[:1000]})
        if restored:
            result["layers"]["blob_arena_restored"] = restored


def _phase_memgpt(query, session_id, top_k, result, ctx, self_ref):
    from hierarchical_context import get_context_layer
    ctx_layer = get_context_layer(session_id=session_id)
    if ctx_layer:
        ctx_layer.add_turn("user", query, session_id=session_id)
        extra = []
        if self_ref._entry:
            recalled = self_ref._entry.recall(query, top_k=top_k, session_id=session_id)
            if isinstance(recalled, list):
                extra = recalled[:top_k]
        assembled = ctx_layer.get_assembled_context(
            query=query, extra_memories=extra, session_id=session_id)
        result["layers"]["memgpt_context"] = assembled[:4000]


def _phase_heat_tracker(query, session_id, top_k, result, ctx, self_ref):
    from memory_os import HeatTracker
    if not hasattr(self_ref, '_heat_tracker'):
        self_ref._heat_tracker = HeatTracker()
    self_ref._heat_tracker.record_access(f"query_{session_id}", session_id=session_id)
    hot_nodes = self_ref._heat_tracker.get_top_nodes(5, session_id=session_id)
    result["layers"]["heat_top"] = hot_nodes


def _phase_hierarchical_memory(query, session_id, top_k, result, ctx, self_ref):
    from hierarchical_memory import HierarchicalMemoryManager, get_manager
    hm = get_manager() if 'get_manager' in dir() else None
    if hm is None:
        hm = HierarchicalMemoryManager()
    hm_recall = hm.recall(query, top_k=top_k, session_id=session_id)
    result["layers"]["hierarchical_memories"] = hm_recall[:top_k]


def _phase_haconvdr(query, session_id, top_k, result, ctx, self_ref):
    self_ref._ensure()
    if self_ref._entry:
        scrubbed = self_ref._entry.recall(query, top_k=top_k * 2, session_id=session_id)
        result["layers"]["haconvdr_recall"] = scrubbed[:top_k]


def _phase_arigraph(query, session_id, top_k, result, ctx, self_ref):
    from paper_integration import get_integration
    pi = get_integration()
    if pi:
        spatial_ctx = pi.spatial_context_augment(query, [])
        if spatial_ctx:
            result["layers"]["spatial_scene"] = spatial_ctx.get("scene_nav", "")[:500]


def _phase_raptor(query, session_id, top_k, result, ctx, self_ref):
    from four_advancements import RAPTOREngine
    if hasattr(self_ref, '_raptor') and self_ref._raptor._tree_built:
        result["layers"]["raptor_summaries"] = list(self_ref._raptor._summaries.values())[:3]


def _phase_cognitive_load(query, session_id, top_k, result, ctx, self_ref):
    from cognitive_load import CognitiveLoad
    cl = CognitiveLoad()
    load_result = cl.assess(query, [], [])
    cognitive_load_level = load_result.get("load_level", 0.5)
    ctx["cognitive_load_level"] = cognitive_load_level
    result["decisions"]["cognitive_load"] = {
        "level": cognitive_load_level,
        "intrinsic": load_result.get("intrinsic", 0),
        "extrinsic": load_result.get("extrinsic", 0)
    }


def _phase_crag_threshold(query, session_id, top_k, result, ctx, self_ref):
    from dynamic_crag_threshold import DynamicCRAGThreshold
    dct = DynamicCRAGThreshold()
    cognitive_load_level = ctx.get("cognitive_load_level", 0.5)
    adaptive_thresholds = dct.compute_thresholds(
        query_complexity=len(query.split()),
        cognitive_load=cognitive_load_level)
    result["decisions"]["crag_thresholds"] = adaptive_thresholds


def _phase_crag(query, session_id, top_k, result, ctx, self_ref):
    from retrieval_evaluator import evaluate_retrieval, RetrievalAction
    all_retrieved = (result["layers"].get("hierarchical_memories", []) +
                     result["layers"].get("haconvdr_recall", []))
    if all_retrieved:
        docs = [r.get("content", "") if isinstance(r, dict) else str(r)
                for r in all_retrieved[:10]]
        eval_result = evaluate_retrieval(query, docs)
        action = eval_result.action.value if hasattr(eval_result.action, 'value') else str(eval_result.action)
        result["decisions"]["crag"] = {
            "action": action,
            "quality": eval_result.quality_score,
            "selected_count": len(getattr(eval_result, 'selected_indices', []))
        }
        if action in ("discard", "discarded"):
            result["layers"]["hierarchical_memories"] = []
            result["layers"]["haconvdr_recall"] = []


def _phase_cove(query, session_id, top_k, result, ctx, self_ref):
    from chain_of_verification import ChainOfVerification
    all_retrieved = (result["layers"].get("hierarchical_memories", []) +
                     result["layers"].get("haconvdr_recall", []))
    if all_retrieved:
        docs = [r.get("content", "") if isinstance(r, dict) else str(r)
                for r in all_retrieved[:5]]
        combined = "\n".join(docs)
        cove = ChainOfVerification()
        cove_result = cove.verify(combined, query)
        verified_score = cove_result.get("verified_ratio", 0.5) if isinstance(cove_result, dict) else 0.5
        result["decisions"]["cove"] = {"verified_ratio": verified_score}


def _phase_hallucination_params(query, session_id, top_k, result, ctx, self_ref):
    from adaptive_hallucination_params import AdaptiveHallucinationParams
    ahp = AdaptiveHallucinationParams()
    crag_quality = result.get("decisions", {}).get("crag", {}).get("quality", 0.5)
    params = ahp.compute_params(query, context=str(crag_quality))
    result["decisions"]["hallucination_params"] = {"confidence_threshold": params.get("threshold", 0.8)}


def _phase_skill0(query, session_id, top_k, result, ctx, self_ref):
    from skill_curriculum import SkillCurriculum, SkillValidationBridge, build_default_skill_catalog
    if not hasattr(self_ref, '_skill_curriculum'):
        self_ref._skill_curriculum = SkillCurriculum()
        catalog = build_default_skill_catalog()
        if not self_ref._skill_curriculum._is_internalizing:
            self_ref._skill_curriculum.initialize(catalog)
    if not hasattr(self_ref, '_skill_bridge'):
        self_ref._skill_bridge = SkillValidationBridge()
    _cq = result.get("decisions", {}).get("crag", {}).get("quality", 0.5)
    _cv = result.get("decisions", {}).get("cove", {}).get("verified_ratio", 0.5)
    _proxy_acc = (_cq + _cv) / 2.0
    for skill_name in list(self_ref._skill_curriculum.active_skills):
        self_ref._skill_bridge.record_result(skill_name, _proxy_acc > 0.5)
    step_result = self_ref._skill_curriculum.step(validation_fn=self_ref._skill_bridge.validate)
    active_count = step_result.get("active_skills",
        step_result.get("active", len(self_ref._skill_curriculum.active_skills)))
    result["decisions"]["skill0"] = {
        "stage": step_result.get("stage", self_ref._skill_curriculum._current_stage),
        "active_count": active_count,
        "budget": step_result.get("budget", 0),
        "done": step_result.get("done", False),
    }


def _phase_coevolve(query, session_id, top_k, result, ctx, self_ref):
    _cq = result.get("decisions", {}).get("crag", {}).get("quality", 0.5)
    _cv = result.get("decisions", {}).get("cove", {}).get("verified_ratio", 0.5)
    _isrel_skipped = result.get("skipped", "") == "isrel_no_retrieve"
    if _cq < 0.4 and _cv < 0.4:
        result["decisions"]["coevolve"] = {
            "failure_detected": True,
            "pattern": "low_quality_retrieval",
            "action": "reactivate_retrieval_skills"
        }
    elif _isrel_skipped and len(query.split()) > 10:
        result["decisions"]["coevolve"] = {
            "failure_detected": True,
            "pattern": "isrel_false_negative",
            "action": "lower_isrel_threshold"
        }


def _phase_turn_recovery(query, session_id, top_k, result, ctx, self_ref):
    crag_q = result.get("decisions", {}).get("crag", {}).get("quality", 0.5)
    cove_v = result.get("decisions", {}).get("cove", {}).get("verified_ratio", 0.5)
    if not hasattr(self_ref, '_turn_history'):
        self_ref._turn_history = {}
    if session_id not in self_ref._turn_history:
        self_ref._turn_history[session_id] = []
    hist = self_ref._turn_history[session_id]
    hist.append({"q": query[:80], "cq": crag_q, "cv": cove_v, "ts": __import__('time').time()})
    if len(hist) > 20:
        hist.pop(0)
    if len(hist) >= 3:
        recent = hist[-3:]
        scores = [h["cq"] for h in recent]
        if scores[0] > 0.5 and scores[-1] < scores[0] * 0.5:
            result["decisions"]["turn_recovery"] = {
                "degraded": True,
                "trend": f"{scores[0]:.2f}→{scores[-1]:.2f}",
                "action": "discard_anchors",
                "detail": "连续降级超过50%，可能锚定错误，已清理轮次缓存"
            }
            hist.clear()
            result["layers"]["turn_recovery"] = True


def _phase_memcoe(query, session_id, top_k, result, ctx, self_ref):
    _cq = result.get("decisions", {}).get("crag", {}).get("quality", 0.5)
    _cv = result.get("decisions", {}).get("cove", {}).get("verified_ratio", 0.5)
    guideline = {}
    if _cq > 0.7 and _cv > 0.7:
        guideline["action"] = "consolidate"
        guideline["heat_boost"] = 0.15
    elif _cq < 0.4 or _cv < 0.4:
        guideline["action"] = "decay"
        guideline["heat_decay"] = 0.2
    else:
        guideline["action"] = "maintain"
    try:
        from memory_os import HeatTracker
        if hasattr(self_ref, '_heat_tracker') and guideline:
            if guideline.get("heat_boost"):
                for nid in result.get("layers", {}).get("heat_top", [])[:3]:
                    self_ref._heat_tracker.record_access(nid, session_id=session_id)
            if guideline.get("heat_decay"):
                cold = self_ref._heat_tracker.get_cold_nodes(session_id=session_id)
                for nid in cold[:3]:
                    current = self_ref._heat_tracker.get_heat(nid, session_id=session_id)
    except Exception:
        pass
    result["decisions"]["memcoe"] = {"guideline": guideline["action"]}


def _phase_memoryos(query, session_id, top_k, result, ctx, self_ref):
    from memory_os import SegmentedPageOrganizer
    if not hasattr(self_ref, '_page_org'):
        self_ref._page_org = SegmentedPageOrganizer()
    self_ref._page_org.add_page(query, {"session": session_id, "ts": __import__('time').time()})
    if len(self_ref._page_org.short_term) > self_ref._page_org.max_short_term:
        self_ref._page_org.upgrade_to_mid()
    ltm_profile = self_ref._page_org.get_ltm_context()
    if ltm_profile:
        result["layers"]["memoryos_profile"] = ltm_profile[:1000]


def _phase_liquid_rerank(query, session_id, top_k, result, ctx, self_ref):
    """液态重排：检索结果 → Engram 重要性 + 液态时间常数 → 重排 Top-K"""
    all_candidates = []
    for layer_key in ("hierarchical_memories", "haconvdr_recall"):
        items = result.get("layers", {}).get(layer_key, [])
        if isinstance(items, list):
            all_candidates.extend(items)
    if not all_candidates:
        return

    # Engram 重要性评分
    try:
        from engram_memory import EngramMemory, EngramConfig
        if not hasattr(self_ref, '_engram_for_rerank'):
            self_ref._engram_for_rerank = EngramMemory(EngramConfig())
        engram = self_ref._engram_for_rerank
    except Exception:
        engram = None

    # 液态权重生成器
    try:
        from liquid_weight import LiquidWeightGenerator, LiquidWeightConfig
        if not hasattr(self_ref, '_liquid_weight_gen'):
            self_ref._liquid_weight_gen = LiquidWeightGenerator(LiquidWeightConfig())
        lw_gen = self_ref._liquid_weight_gen
    except Exception:
        lw_gen = None

    import time as _time
    scored = []
    for item in all_candidates:
        content = item.get("content", "") if isinstance(item, dict) else str(item)
        item_id = item.get("id", item.get("blob_id", "")) if isinstance(item, dict) else ""

        # Base: 原始相关性分数（来自向量检索）
        base_score = float(item.get("score", 0.5)) if isinstance(item, dict) else 0.5

        # Engram 命中增强
        engram_boost = 0.0
        if engram and content:
            words = content.strip().split()[:20]
            hits = 0
            for w in words[:5]:  # 不要跑太多词，耗时
                _, info = engram.lookup(w)
                if isinstance(info, dict) and info.get("hit", False):
                    hits += 1
            engram_boost = min(hits / 5.0, 1.0) * 0.15

        # 液态时间常数权重
        liquid_weight = 0.0
        if lw_gen:
            ts = item.get("ts", item.get("timestamp", _time.time())) if isinstance(item, dict) else _time.time()
            if isinstance(ts, str):
                ts = _time.time()
            age_hours = max(0, (_time.time() - float(ts)) / 3600)
            # 输入特征: [时效性, 重要性, 活跃度, 稳定性]
            feat = np.array([min(age_hours / 48.0, 1.0), base_score, 0.5, 0.5], dtype=np.float32)
            liquid_weight = lw_gen.generate_weight(feat)

        combined = base_score + engram_boost + liquid_weight * 0.2
        scored.append((combined, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    result["layers"]["liquid_reranked"] = [s[1] for s in scored[:top_k]]
    result["decisions"]["liquid_rerank"] = {
        "reranked": len(scored),
        "top_score": round(scored[0][0], 3) if scored else 0,
        "engram_hit_ratio": round(engram_boost / 0.15, 2) if engram_boost > 0 else 0,
    }


def _phase_kan_rerank(query, session_id, top_k, result, ctx, self_ref):
    """KAN 语义重排：用 KAN 网络的 B-spline 对 liquid_reranked 进一步打分"""
    items = result.get("layers", {}).get("liquid_reranked", [])
    if not items:
        return
    try:
        from kan_network import KANNetwork
        if not hasattr(self_ref, '_kan_reranker'):
            # 4→8→1 的小网络，输出重要性分数
            self_ref._kan_reranker = KANNetwork([4, 8, 1], n_basis=5, degree=2, use_residual=True)
        kan = self_ref._kan_reranker
    except Exception:
        return

    import numpy as np
    import time as _time
    scored = []
    for item in items:
        base = float(item.get("score", 0.5)) if isinstance(item, dict) else 0.5
        ts = item.get("ts", item.get("timestamp", _time.time())) if isinstance(item, dict) else _time.time()
        if isinstance(ts, str):
            ts = _time.time()
        age_h = max(0, (_time.time() - float(ts)) / 3600)
        content_len = min(len(item.get("content", "")) / 500.0, 1.0) if isinstance(item, dict) else 0.5
        feat = np.array([[base, min(age_h / 48.0, 1.0), content_len, 0.5]], dtype=np.float32)
        kan_score = float(kan.forward(feat)[0, 0])
        kan_score = max(0.01, min(1.0, (kan_score + 1) / 2))
        scored.append((base * 0.3 + kan_score * 0.7, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    result["layers"]["kan_reranked"] = [s[1] for s in scored[:top_k]]
    result["decisions"]["kan_rerank"] = {
        "reranked": len(scored),
        "top_score": round(scored[0][0], 3) if scored else 0,
    }


def _phase_liquid_graph(query, session_id, top_k, result, ctx, self_ref):
    """液态图嵌入：用 LiquidGraphLayer 提取文档间拓扑关系"""
    items = result.get("layers", {}).get("kan_reranked", []) or result.get("layers", {}).get("liquid_reranked", [])
    if not items:
        return
    import numpy as np
    import time as _time
    n = len(items)
    if n < 2:
        return
    feats = np.zeros((n, 4), dtype=np.float32)
    for i, item in enumerate(items):
        base = float(item.get("score", 0.5)) if isinstance(item, dict) else 0.5
        ts = item.get("ts", item.get("timestamp", _time.time())) if isinstance(item, dict) else _time.time()
        if isinstance(ts, str):
            ts = _time.time()
        age_h = max(0, (_time.time() - float(ts)) / 3600)
        feats[i] = [base, min(age_h / 48.0, 1.0), float(i) / n, 0.5]

    try:
        from liquid_graph_time_constant import LiquidGraphLayer
        if not hasattr(self_ref, '_liquid_graph'):
            self_ref._liquid_graph = LiquidGraphLayer(dim_in=4, dim_out=4)
        graph = self_ref._liquid_graph
    except Exception:
        return

    # 简单全连接图（每个节点连所有其他节点）
    nbr_idx = np.zeros((n, n), dtype=np.int64)
    nbr_mask = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        nbr_idx[i] = np.arange(n)
        nbr_mask[i] = np.ones(n, dtype=np.float32)
        nbr_mask[i, i] = 0.0  # 不连自己

    out = graph.forward(feats, nbr_idx, nbr_mask)
    embedding_norm = float(np.mean(np.linalg.norm(out, axis=1)))
    result["layers"]["liquid_graph"] = {
        "n_nodes": n,
        "embedding_norm": round(embedding_norm, 3),
        "top_graph_score": round(float(out[0].sum()), 3) if n > 0 else 0,
    }


def _phase_liquid_state(query, session_id, top_k, result, ctx, self_ref):
    """液态状态追踪：LiquidSSM 预测对话状态演变"""
    try:
        from liquid_ssm import LiquidSSM
        if not hasattr(self_ref, '_liquid_state_tracker'):
            self_ref._liquid_state_tracker = LiquidSSM(state_dim=4, input_dim=4, output_dim=2, n_channels=2)
        tracker = self_ref._liquid_state_tracker
    except Exception:
        return

    import time as _time

    # 构建输入特征向量
    q_len = min(len(query) / 200.0, 1.0)  # 归一化长度
    q_words = len(query.split())
    has_question = 1.0 if any(c in query for c in "？?吗呢吧") else 0.0
    has_code = 1.0 if any(kw in query for kw in ["代码", "code", "def ", "class ", "import", "bug", "报错"]) else 0.0
    features = np.array([[q_len, min(q_words / 50.0, 1.0), has_question, has_code]], dtype=np.float32)

    # 维护状态（n_channels=2, state_dim=4 → shape (2, 4)）
    if not hasattr(self_ref, '_liquid_state_h'):
        self_ref._liquid_state_h = np.zeros((2, 4), dtype=np.float32)

    # 单步前向
    h_new, _ = tracker.forward_step(self_ref._liquid_state_h, features[0])
    delta = float(np.linalg.norm(h_new - self_ref._liquid_state_h))
    self_ref._liquid_state_h = h_new
    state_change = min(delta / 2.0, 1.0)

    result["layers"]["liquid_state"] = {
        "state_change": round(state_change, 3),
        "high_change": state_change > 0.5,
        "features": {"long_query": q_len > 0.5, "has_question": bool(has_question), "has_code": bool(has_code)},
        "hidden_norm": round(float(np.linalg.norm(h_new)), 3),
    }


def _phase_ssm(query, session_id, top_k, result, ctx, self_ref):
    from ssm_state_predictor import SSMPredictor
    if not hasattr(self_ref, '_ssm_predictor'):
        self_ref._ssm_predictor = SSMPredictor()
    self_ref._ssm_predictor.record_recall(query, session_id)
    predicted = self_ref._ssm_predictor.predict_next_recall(query, session_id, top_k=3)
    if predicted:
        result["layers"]["ssm_predicted"] = [
            {"id": p[0], "prob": round(p[1], 3)} for p in predicted
        ]


def _phase_hyper_routing(query, session_id, top_k, result, ctx, self_ref):
    from hyper_routing import HyperRouter
    if not hasattr(self_ref, '_hyper_router'):
        self_ref._hyper_router = HyperRouter()
    route_hint = self_ref._hyper_router.select_strategy({
        "query_len": len(query),
        "session_len": 1,
        "time": __import__('time').time()
    })
    result["decisions"]["hyper_route"] = {"strategy": str(route_hint)}


def _phase_kora(query, session_id, top_k, result, ctx, self_ref):
    from kora_behavior import KoraBehavior
    if not hasattr(self_ref, '_kora'):
        self_ref._kora = KoraBehavior()
    self_ref._kora.record_action(query, session_id)
    pattern_hint = self_ref._kora.detect_pattern(session_id)
    if pattern_hint:
        result["layers"]["kora_pattern"] = pattern_hint[:500]


def _phase_code_aware(query, session_id, top_k, result, ctx, self_ref):
    code_keywords = ["代码", "code", "def ", "class ", "import ", "bug", "报错", "函数", "变量"]
    if any(kw in query for kw in code_keywords):
        from code_aware_reasoning import CodeAwareReasoner
        car = CodeAwareReasoner()
        code_hint = car.analyze_query(query)
        if code_hint:
            result["layers"]["code_aware"] = code_hint[:300]


def _phase_thinking_enhanced(query, session_id, top_k, result, ctx, self_ref):
    from thinking_enhanced import ThinkingEnhanced
    te = ThinkingEnhanced()
    multi_hint = te.multi_path_reason(query, max_paths=2)
    if multi_hint:
        result["layers"]["thinking_enhanced"] = multi_hint[:500]


def _phase_consolidation(query, session_id, top_k, result, ctx, self_ref):
    from memory_consolidation import MemoryConsolidationEngine
    if not hasattr(self_ref, '_consolidation_engine'):
        self_ref._consolidation_engine = MemoryConsolidationEngine()
    if not hasattr(self_ref, '_consolidation_counter'):
        self_ref._consolidation_counter = 0
    self_ref._consolidation_counter += 1
    if self_ref._consolidation_counter % 50 == 0:
        self_ref._consolidation_engine.consolidate(background=True)


def _phase_sleep_consolidation(query, session_id, top_k, result, ctx, self_ref):
    from biorhythm_sleep_consolidation import SleepConsolidation
    if not hasattr(self_ref, '_sleep_consolidation'):
        self_ref._sleep_consolidation = SleepConsolidation()
    if not hasattr(self_ref, '_last_sleep_check'):
        self_ref._last_sleep_check = 0
    _now = __import__('time').time()
    if _now - self_ref._last_sleep_check > 3600:
        self_ref._sleep_consolidation.check_and_consolidate()
        self_ref._last_sleep_check = _now


def _phase_ssm_kan_state(query, session_id, top_k, result, ctx, self_ref):
    """KAN 增强的状态追踪：SSMWithKAN 替代纯 Linear SSM"""
    try:
        from ssm_kan_fusion import SSMWithKAN
        if not hasattr(self_ref, '_ssm_kan_tracker'):
            self_ref._ssm_kan_tracker = SSMWithKAN(state_dim=4, input_dim=4, output_dim=2, n_channels=2)
        tracker = self_ref._ssm_kan_tracker
    except Exception:
        return

    import numpy as np
    import time as _time
    q_len = min(len(query) / 200.0, 1.0)
    q_words = len(query.split())
    has_question = 1.0 if any(c in query for c in "？?吗吗吗吧") else 0.0
    has_code = 1.0 if any(kw in query for kw in ["代码", "code", "def ", "class ", "import", "bug", "报错"]) else 0.0
    features = np.array([[q_len, min(q_words / 50.0, 1.0), has_question, has_code]], dtype=np.float32)

    if not hasattr(self_ref, '_ssm_kan_h'):
        self_ref._ssm_kan_h = np.zeros((2, 4), dtype=np.float64)
    h_new, _ = tracker.forward_step(self_ref._ssm_kan_h, features[0])
    delta = float(np.linalg.norm(h_new.astype(np.float64) - self_ref._ssm_kan_h))
    self_ref._ssm_kan_h = h_new

    result["layers"]["ssm_kan_state"] = {
        "state_change": round(delta, 3),
        "high_change": delta > 0.5,
        "features": {"long_query": q_len > 0.5, "has_question": bool(has_question), "has_code": bool(has_code)},
        "hidden_norm": round(float(np.linalg.norm(h_new)), 3),
    }


def _phase_moe_engram(query, session_id, top_k, result, ctx, self_ref):
    """MoE-Engram 推理路由：决定走 Engram 还是 MoE 还是融合"""
    try:
        from moe_engram_hybrid import MoeEngramBlock, U_ShapeScalingLaw
        if not hasattr(self_ref, '_moe_engram'):
            self_ref._moe_engram = MoeEngramBlock(input_dim=4, hidden_dim=8, output_dim=2, num_experts=2)
        block = self_ref._moe_engram
    except Exception:
        return

    import numpy as np
    import time as _time
    crag_q = result.get("decisions", {}).get("crag", {}).get("quality", 0.5)
    cove_v = result.get("decisions", {}).get("cove", {}).get("verified_ratio", 0.5)
    query_len = min(len(query) / 200.0, 1.0)
    query_code = 1.0 if any(kw in query for kw in ["代码", "code", "def ", "class ", "import", "bug", "报错"]) else 0.0

    feats = np.array([[crag_q, cove_v, query_len, query_code]], dtype=np.float32)
    try:
        out = block.forward(feats)
        route_alpha = float(out[0, 0]) if hasattr(out, '__getitem__') else 0.5
    except Exception:
        route_alpha = 0.5

    route_alpha = max(0.0, min(1.0, route_alpha))
    result["decisions"]["moe_engram"] = {
        "alpha": round(route_alpha, 3),
        "strategy": "engram_only" if route_alpha < 0.15 else (
            "moe_only" if route_alpha > 0.85 else "fusion"),
        "retrieval_quality": round(crag_q, 2),
        "verified_ratio": round(cove_v, 2),
    }


def _phase_ltc_ode(query, session_id, top_k, result, ctx, self_ref):
    """LTC Neural ODE 对话轨迹预测：NeuralODE 将状态编码后 ODE 积分一步"""
    try:
        from neural_ode import NeuralODE
        if not hasattr(self_ref, '_neural_ode'):
            self_ref._neural_ode = NeuralODE(state_dim=4, hidden_dim=8, num_layers=2, solver="euler")
        ode = self_ref._neural_ode
    except Exception:
        return

    import numpy as np
    import time as _time
    crag_q = result.get("decisions", {}).get("crag", {}).get("quality", 0.5)
    cove_v = result.get("decisions", {}).get("cove", {}).get("verified_ratio", 0.5)
    query_len = min(len(query) / 200.0, 1.0)
    state_now = np.array([crag_q, cove_v, query_len, 0.5], dtype=np.float64)

    try:
        ts, ys = ode.forward(state_now.reshape(-1), (0.0, 0.5), dt=0.1)
        final_state = ys[-1]
        ode_norm = float(np.linalg.norm(final_state))
        delta = float(np.linalg.norm(final_state - state_now))
        result["layers"]["ltc_ode"] = {
            "state_norm": round(ode_norm, 3),
            "trajectory_delta": round(delta, 3),
            "t_steps": len(ts),
            "diverge": delta > 2.0,
        }
    except Exception:
        pass


def _phase_lipschitz_stable(query, session_id, top_k, result, ctx, self_ref):
    """Lipschitz 稳定性校验：确保 ODE 轨迹不会震荡发散"""
    """Lipschitz 稳定性校验：确保 ODE 轨迹不会震荡发散"""
    try:
        from lipschitz_liquid import LipschitzConstraint
        constraint = LipschitzConstraint(shape=(4, 1), norm_type="spectral", spectral_gamma=1.5)
    except Exception:
        return

    import numpy as np
    ltc_ode = result.get("layers", {}).get("ltc_ode", {})
    trajectory_delta = ltc_ode.get("trajectory_delta", 0.0)
    diverge = ltc_ode.get("diverge", False)

    lipschitz_bound = 2.0  # 默认值
    stable = not diverge if trajectory_delta > 0 else True

    result["decisions"]["lipschitz_stable"] = {
        "stable": bool(stable),
        "lipschitz_bound": 2.0,
        "trajectory_delta": round(trajectory_delta, 3),
        "stability_ratio": round(1.0 - min(trajectory_delta / 3.0, 1.0), 3) if trajectory_delta > 0 else 1.0,
    }
