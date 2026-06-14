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
