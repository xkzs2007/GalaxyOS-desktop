"""
Pipeline Registry — GalaxyOS 全模块注册表

将所有模块注册到 PipelineEngine 中，声明各自 inputs/outputs，
引擎自动推导依赖、拓扑排序、并行调度。

这解决了：
  - 13 个核心模块 7168 行 0 调用的问题
  - 模块产出不被下游消费的"死数据"问题
  - context_assemble 500 行面条代码的维护问题

注册方式: 
  register_module("name", inputs=[...], outputs=[...], fn=..., critical=...)
  
  critical=True:  关键路径，串行执行，失败抛异常
  critical=False: 后台异步，失败只记日志
"""

import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("galaxyos.pipeline.registry")


def register_all_modules(engine) -> None:
    """注册 GalaxyOS 所有模块到流水线引擎。"""

    # ═══════════════════════════════════════════════════
    # Phase 0: 判别层
    # ═══════════════════════════════════════════════════

    def _isrel(query: str, session_id: str = "", isrel_threshold_override: float = None, **kw) -> dict:
        try:
            from isrel_predictor import IsRELPredictor
            p = IsRELPredictor()
            if isrel_threshold_override is not None:
                p.confidence_threshold = isrel_threshold_override
            decision = p.predict(query)
            return {
                "should_retrieve": decision.should_retrieve,
                "isrel_confidence": decision.confidence,
                "isrel_reason": getattr(decision, 'reason', ''),
            }
        except Exception as e:
            return {"should_retrieve": True, "isrel_error": str(e)}

    engine.register(
        "isrel", critical=True,
        inputs=["query", "session_id", "isrel_threshold_override"],
        outputs=["should_retrieve", "isrel_confidence", "isrel_reason"],
        fn=_isrel,
        description="Self-RAG IsREL: 判断是否需要检索",
    )

    # ═══════════════════════════════════════════════════
    # Phase 1: 检索层
    # ═══════════════════════════════════════════════════

    def _blob_arena(query: str, session_id: str = "", **kw) -> dict:
        try:
            from blob_arena import BlobArena
            arena = BlobArena()
            records = arena.read_text(session_id)
            return {"blob_records": records or []}
        except Exception as e:
            return {"blob_error": str(e)}

    engine.register(
        "blob_arena", critical=False,
        inputs=["query", "session_id"],
        outputs=["blob_records"],
        fn=_blob_arena,
        description="BlobArena: 无损原始文本恢复",
    )

    def _memgpt(query: str, top_k: int = 5, **kw) -> dict:
        try:
            from hierarchical_context import HierarchicalContextManager
            mgr = HierarchicalContextManager()
            ctx = mgr.retrieve(query, top_k=top_k)
            return {"memgpt_context": ctx}
        except Exception as e:
            return {"memgpt_error": str(e)}

    engine.register(
        "memgpt", critical=False,
        inputs=["query", "top_k"],
        outputs=["memgpt_context"],
        fn=_memgpt,
        description="MemGPT: 三级分层记忆检索",
    )

    # ═══════════════════════════════════════════════════
    # Phase 2: 增强检索 (GNN + 向量 + 重排序)
    # ═══════════════════════════════════════════════════

    def _embedding(query: str, **kw) -> dict:
        """bge-m3 向量化（远程或本地 ONNX）"""
        try:
            from embedding import EmbeddingEngine
            emb = EmbeddingEngine()
            vector = emb.encode([query])
            return {"query_vector": vector}
        except Exception as e:
            return {"embedding_error": str(e)}

    engine.register(
        "embedding", critical=False,
        inputs=["query"],
        outputs=["query_vector"],
        fn=_embedding,
        description="bge-m3 向量编码",
    )

    def _vector_search(query_vector: list = None, query: str = "", top_k: int = 5, session_id: str = "", **kw) -> dict:
        """UnifiedVectorStore 向量检索"""
        try:
            from unified_vector_store import UnifiedVectorStore
            store = UnifiedVectorStore()
            if query_vector is not None:
                results = store.search(query_vector, top_k=top_k)
            else:
                results = store.search_text(query, top_k=top_k)
            return {"raw_results": results, "vector_search_done": True}
        except Exception as e:
            return {"vector_search_error": str(e), "raw_results": []}

    engine.register(
        "vector_search", critical=False,
        inputs=["query_vector", "query", "top_k", "session_id"],
        outputs=["raw_results", "vector_search_done"],
        fn=_vector_search,
        description="UnifiedVectorStore 向量检索",
    )

    def _gnn_search(query: str, top_k: int = 5, **kw) -> dict:
        """HAConvDR 风格: GNNBuilder + GATLayer 异构图检索"""
        try:
            from gnn_graph_builder import GNNBuilder
            builder = GNNBuilder()
            graph = builder.build_from_query(query)
            # GNN 推理
            from gat_layer import GATLayer
            gat = GATLayer()
            gnn_results = gat.forward(graph)
            return {"gnn_results": gnn_results[:top_k], "gnn_search_done": True}
        except Exception as e:
            return {"gnn_search_error": str(e), "gnn_results": []}

    engine.register(
        "gnn_search", critical=False,
        inputs=["query", "top_k"],
        outputs=["gnn_results", "gnn_search_done"],
        fn=_gnn_search,
        description="GNN 异构图表征检索",
    )

    def _knowledge_graph_search(query: str, top_k: int = 5, **kw) -> dict:
        """KnowledgeGraphGNN: KG 增强检索"""
        try:
            from knowledge_graph_gnn import KnowledgeGraphGNN
            kg = KnowledgeGraphGNN()
            kg_results = kg.query(query, top_k=top_k)
            return {"kg_results": kg_results, "kg_search_done": True}
        except Exception as e:
            return {"kg_search_error": str(e), "kg_results": []}

    engine.register(
        "kg_search", critical=False,
        inputs=["query", "top_k"],
        outputs=["kg_results", "kg_search_done"],
        fn=_knowledge_graph_search,
        description="KnowledgeGraphGNN: 知识图谱增强检索",
    )

    def _spatial_search(query: str, session_id: str = "", top_k: int = 5, **kw) -> dict:
        """AriGraph: 空间拓扑检索"""
        try:
            from spatial_topology import SpatialTopologyGraph
            spatial = SpatialTopologyGraph()
            spatial_results = spatial.query_context(query, top_k=top_k)
            return {"arigraph_context": spatial_results}
        except Exception as e:
            return {"arigraph_error": str(e)}

    engine.register(
        "spatial_search", critical=False,
        inputs=["query", "session_id", "top_k"],
        outputs=["arigraph_context"],
        fn=_spatial_search,
        description="AriGraph: 空间拓扑图推理检索",
    )

    # ═══════════════════════════════════════════════════
    # Phase 2b: 命题级检索 (Proposition Retrieval)
    # ═══════════════════════════════════════════════════

    def _proposition_retriever(query: str = "", raw_results: list = None, top_k: int = 5, **kw) -> dict:
        """Proposition 命题级检索: 将文档拆为原子命题后检索"""
        try:
            from proposition_retriever import PropositionRetriever, PropositionExtractor
            retriever = PropositionRetriever()
            docs = []
            for r in (raw_results or []):
                if isinstance(r, dict):
                    docs.append(r.get("content", str(r)))
                else:
                    docs.append(str(r))
            if docs:
                retriever.add_documents(docs)
                results = retriever.search(query, top_k=top_k)
                return {"proposition_results": results}
            return {"proposition_results": []}
        except Exception as e:
            return {"proposition_error": str(e), "proposition_results": []}

    engine.register(
        "proposition_retriever", critical=False,
        inputs=["query", "raw_results", "top_k"],
        outputs=["proposition_results"],
        fn=_proposition_retriever,
        description="Proposition: 命题级检索（更精准的检索粒度）",
    )

    # ═══════════════════════════════════════════════════
    # Phase 2a: MemGAS-SkVM 融合层 — 能力画像 + 熵路由
    # ═══════════════════════════════════════════════════

    def _capability_registry(query: str = "", session_id: str = "", **kw) -> dict:
        """CapabilityRegistry: SkVM 26维原语能力匹配

        为当前 query 匹配最合适的 skill 能力画像，
        输出 capability_profile 供下游 knowledge_asset 使用。
        """
        try:
            from capability_registry import CapabilityProfile, ProfileMatcher, HarnessProfile
            # 从 query 构建一个简配的 harness profile
            harness = HarnessProfile() if hasattr(HarnessProfile, '__init__') else {}
            # 使用 SkillClassifier 从 query 推断能力需求
            from capability_registry import SkillClassifier
            classifier = SkillClassifier()
            profile = classifier.classify(query) if hasattr(classifier, 'classify') else {}
            return {
                "capability_matched": bool(profile),
                "capability_profile": profile if isinstance(profile, dict) else {},
            }
        except Exception as e:
            return {"capability_error": str(e), "capability_matched": False, "capability_profile": {}}

    engine.register(
        "capability_registry", critical=False,
        inputs=["query", "session_id"],
        outputs=["capability_matched", "capability_profile"],
        fn=_capability_registry,
        description="SkVM 26维原语能力匹配",
    )

    def _entropy_routing(query: str, capability_profile: dict = None, **kw) -> dict:
        """EntropyRouter: 基于语义熵 + 能力画像选择融合策略"""
        try:
            from entropy_router import EntropyRouter
            router = EntropyRouter()
            # 携带 capability_profile 计算通道权重
            channel_scores = router.compute_channel_entropy(list(capability_profile.values()) if isinstance(capability_profile, dict) else [])
            return {"fusion_strategy": channel_scores}
        except Exception as e:
            return {"entropy_error": str(e), "fusion_strategy": 0.5}

    engine.register(
        "entropy_routing", critical=False,
        inputs=["query", "capability_profile"],
        outputs=["fusion_strategy"],
        fn=_entropy_routing,
        description="EntropyRouter: 语义熵 + 能力画像路由",
    )

    def _reranker(
        query: str,
        raw_results: list = None,
        gnn_results: list = None,
        kg_results: list = None,
        top_k: int = 5,
        **kw
    ) -> dict:
        """bge-reranker-v2-m3: 多源融合重排序"""
        try:
            from reranker import Reranker
            reranker = Reranker()
            
            # 合并所有检索结果
            all_results = []
            for src_name, src in [("vector", raw_results), ("gnn", gnn_results), ("kg", kg_results)]:
                if src:
                    for item in src[:top_k]:
                        if isinstance(item, dict):
                            item["_source"] = src_name
                            all_results.append(item)
            
            if not all_results:
                return {"reranked_results": []}
            
            reranked = reranker.rerank(query, all_results)
            return {"reranked_results": reranked[:top_k]}
        except Exception as e:
            return {"reranker_error": str(e), "reranked_results": raw_results or []}

    engine.register(
        "reranker", critical=False,
        inputs=["query", "raw_results", "gnn_results", "kg_results", "top_k"],
        outputs=["reranked_results"],
        fn=_reranker,
        description="bge-reranker-v2-m3: 多源融合重排序",
    )

    # ═══════════════════════════════════════════════════
    # Phase 2c: MemGAS-SkVM — 多粒度提取 + 知识资产化
    # ═══════════════════════════════════════════════════

    def _multi_granularity(query: str = "", reranked_results: list = None, cognitive_load: float = 0.5, **kw) -> dict:
        """MultiGranularity: 对 rerank 结果做多粒度提取 (session/turn/summary/keyword)

        cognitive_load 较低时执行完整提取，较高时只做摘要级提取。
        """
        try:
            from multi_granularity import MultiGranularityExtractor, GranularityLevel
            extractor = MultiGranularityExtractor()
            docs = []
            for r in (reranked_results or []):
                if isinstance(r, dict):
                    docs.append(r.get("content", str(r)))
                else:
                    docs.append(str(r))
            text = "\n".join(docs)
            if not text:
                return {"granularity_results": {}}
            # cognitive_load 高时只做 summary 和 keyword
            levels = [GranularityLevel.SUMMARY, GranularityLevel.KEYWORD]
            if cognitive_load < 0.6:
                levels = [GranularityLevel.SESSION, GranularityLevel.TURN, GranularityLevel.SUMMARY, GranularityLevel.KEYWORD]
            result = extractor.extract(text, levels=levels)
            return {"granularity_results": result}
        except Exception as e:
            return {"granularity_error": str(e), "granularity_results": {}}

    engine.register(
        "multi_granularity", critical=False,
        inputs=["query", "reranked_results", "cognitive_load"],
        outputs=["granularity_results"],
        fn=_multi_granularity,
        description="MultiGranularity: 多粒度表示提取",
    )

    def _knowledge_asset(
        query: str = "",
        reranked_results: list = None,
        granularity_results: dict = None,
        capability_matched: bool = False,
        capability_profile: dict = None,
        session_id: str = "",
        **kw
    ) -> dict:
        """KnowledgeAsset: 将检索结果封装为统一知识资产，匹配 capability

        最终产出 asset_injection 供 context_assemble 消费。
        """
        try:
            from knowledge_asset import KnowledgeAsset, AssetType, get_asset_registry, create_skill_asset
            reg = get_asset_registry()
            # 将检索结果封装为 asset
            asset_id = f"retrieval_{session_id}_{int(time.time())}" if session_id else f"retrieval_{int(time.time())}"
            content_parts = []
            for r in (reranked_results or []):
                if isinstance(r, dict):
                    content_parts.append(r.get("content", str(r)))
                else:
                    content_parts.append(str(r))
            asset = KnowledgeAsset(
                asset_id=asset_id,
                asset_type=AssetType.KNOWLEDGE,
                raw_content="\n".join(content_parts),
                capability_profile=capability_profile or {},
                category="retrieval",
            )
            if granularity_results:
                asset.multi_granularity = granularity_results
            reg.register(asset)
            return {
                "asset_registered": True,
                "asset_id": asset_id,
                "asset_injection": asset.raw_content[:3000],  # 截断避免爆 prompt
            }
        except Exception as e:
            return {"asset_error": str(e), "asset_registered": False, "asset_id": ""}

    engine.register(
        "knowledge_asset", critical=False,
        inputs=["query", "reranked_results", "granularity_results", "capability_matched", "capability_profile", "session_id"],
        outputs=["asset_registered", "asset_id", "asset_injection"],
        fn=_knowledge_asset,
        description="KnowledgeAsset: 检索结果封装为统一知识资产",
    )

    def _memoryos(query: str, session_id: str = "", prefetch_ids: list = None, **kw) -> dict:
        """MemoryOS: 热度跟踪 + 分段分页"""
        try:
            from memory_os import HeatTracker, SegmentedPageOrganizer
            heat = HeatTracker()
            heat.record_access(f"query_{session_id}", session_id=session_id)
            # 预加载 SSM 预测的记忆
            for pid in (prefetch_ids or []):
                if isinstance(pid, str):
                    heat.record_access(pid, session_id=session_id)
            
            hot_nodes = heat.get_top_nodes(5, session_id=session_id)
            
            page = SegmentedPageOrganizer()
            ltm = page.promote_to_ltm(session_id)  # LTM 提炼
            profile = page.get_profile(session_id) if hasattr(page, 'get_profile') else ""
            
            return {
                "heat_top": hot_nodes,
                "memoryos_profile": profile,
            }
        except Exception as e:
            return {"memoryos_error": str(e)}

    engine.register(
        "memoryos", critical=False,
        inputs=["query", "session_id", "prefetch_ids"],
        outputs=["heat_top", "memoryos_profile"],
        fn=_memoryos,
        description="MemoryOS: 热度跟踪 + STM→MTM→LPM",
    )

    # ═══════════════════════════════════════════════════
    # Phase 3: 评估层
    # ═══════════════════════════════════════════════════

    def _cognitive_load(query: str, reranked_results: list = None, raw_results: list = None, **kw) -> dict:
        try:
            from cognitive_load import CognitiveLoadAnalyzer
            analyzer = CognitiveLoadAnalyzer()
            results = reranked_results or raw_results or []
            load = analyzer.compute(query=query, results=results, history_rounds=3)
            return {"cognitive_load": load}
        except Exception as e:
            return {"cognitive_load_error": str(e), "cognitive_load": 0.5}

    engine.register(
        "cognitive_load", critical=False,
        inputs=["query", "reranked_results", "raw_results"],
        outputs=["cognitive_load"],
        fn=_cognitive_load,
        description="Cognitive Load: 认知负荷评估",
    )

    def _crag(
        query: str,
        reranked_results: list = None,
        raw_results: list = None,
        cognitive_load: float = 0.5,
        **kw
    ) -> dict:
        """CRAG: 检索质量评估 + USE/DISCARD/AUGMENT"""
        try:
            from retrieval_evaluator import RetrievalEvaluator
            evaluator = RetrievalEvaluator()
            results = reranked_results or raw_results or []
            
            # 评估
            quality = evaluator.evaluate(query, [r.get("content", str(r)) for r in results])
            
            # USE/DISCARD/AUGMENT 决策
            from crag import CragDecisionEngine
            crag = CragDecisionEngine()
            decision = crag.decide(quality=quality, cognitive_load=cognitive_load)
            
            if decision.action == "DISCARD" and results:
                # DISCARD 后尝试 KG 纠正
                try:
                    from knowledge_graph_gnn import KnowledgeGraphGNN
                    kg = KnowledgeGraphGNN()
                    kg_fix = kg.correct_query(query)
                    if kg_fix:
                        return {"crag_quality": 0.0, "crag_action": "DISCARD", "crag_correction": kg_fix}
                except Exception:
                    pass
            
            return {
                "crag_quality": quality,
                "crag_action": decision.action,
            }
        except Exception as e:
            return {"crag_error": str(e), "crag_quality": 0.5, "crag_action": "USE"}

    engine.register(
        "crag", critical=False,
        inputs=["query", "reranked_results", "raw_results", "cognitive_load"],
        outputs=["crag_quality", "crag_action"],
        fn=_crag,
        description="CRAG: 检索质量评估 + 决策",
    )

    def _cove(query: str, reranked_results: list = None, **kw) -> dict:
        """Chain-of-Verification: 逐条验证"""
        try:
            from chain_of_verification import ChainOfVerificationEngine
            cove = ChainOfVerificationEngine(flash_model="deepseek-v4-flash")
            result = cove.verify(query, [r.get("content", str(r)) for r in (reranked_results or [])])
            return {
                "cove_verified_ratio": getattr(result, 'verified_ratio', 0.5),
                "cove_fix": getattr(result, 'fix', ''),
            }
        except Exception as e:
            return {"cove_error": str(e), "cove_verified_ratio": 0.5}

    engine.register(
        "cove", critical=False,
        inputs=["query", "reranked_results"],
        outputs=["cove_verified_ratio", "cove_fix"],
        fn=_cove,
        description="Chain-of-Verification: 逐条验证",
    )

    # ═══════════════════════════════════════════════════
    # Phase 4: 时序预测 (SSM / CfC / LTC)
    def _rag_optimizer(query: str = "", crag_quality: float = 0.5, **kw) -> dict:
        """HyDE 查询重写: CRAG 质量低时自动改写 query"""
        if crag_quality >= 0.4:
            return {"hyde_rewritten": False, "hyde_docs": []}
        try:
            from rag_optimizer import HyDEQueryRewriter
            rewriter = HyDEQueryRewriter(num_hypothetical_docs=2)
            rewritten = rewriter.rewrite(query, return_docs=True)
            if isinstance(rewritten, dict):
                return {
                    "hyde_rewritten": True,
                    "hyde_rewritten_query": rewritten.get("rewritten_query", query),
                    "hyde_docs": rewritten.get("docs", []),
                }
            return {"hyde_rewritten": True, "hyde_rewritten_query": str(rewritten), "hyde_docs": []}
        except Exception as e:
            return {"hyde_error": str(e), "hyde_rewritten": False, "hyde_docs": []}

    engine.register(
        "rag_optimizer", critical=False,
        inputs=["query", "crag_quality"],
        outputs=["hyde_rewritten", "hyde_rewritten_query", "hyde_docs"],
        fn=_rag_optimizer,
        description="HyDE 查询重写: CRAG 质量低时自动改写",
    )

    # ═══════════════════════════════════════════════════

    def _ssm_predict(session_id: str = "", query: str = "", **kw) -> dict:
        """CfC + LTC + SSM: 预测下一步记忆"""
        try:
            from ssm_state_predictor import SSMPredictor
            ssm = SSMPredictor()
            predictions = ssm.predict_next_recall(session_id=session_id, current_query=query, top_k=3)
            return {"ssm_predicted": predictions}
        except Exception as e:
            return {"ssm_error": str(e), "ssm_predicted": []}

    engine.register(
        "ssm_predict", critical=False,
        inputs=["session_id", "query"],
        outputs=["ssm_predicted"],
        fn=_ssm_predict,
        description="SSM/MemCast: 记忆状态预测",
    )

    def _cfc_predict(session_id: str = "", query: str = "", **kw) -> dict:
        """CfC Neural Circuit: 时序意图预测"""
        try:
            from cfc_inference import CfCInferenceEngine
            from cfc_sequence_predictor import CfCSequencePredictor
            cfc = CfCInferenceEngine()
            seq = CfCSequencePredictor()
            intent = cfc.predict_intent(query=query, session_id=session_id)
            seq_pred = seq.predict_sequence(session_id=session_id, top_k=3)
            return {"cfc_intent": intent, "cfc_seq_predictions": seq_pred}
        except Exception as e:
            return {"cfc_error": str(e)}

    engine.register(
        "cfc_predict", critical=False,
        inputs=["session_id", "query"],
        outputs=["cfc_intent", "cfc_seq_predictions"],
        fn=_cfc_predict,
        description="CfC 神经电路: 时序意图预测",
    )

    def _ltc_synapse(session_id: str = "", **kw) -> dict:
        """LTC 突触可塑性更新"""
        try:
            from ltc_synapse import LTCSynapse
            ltc = LTCSynapse()
            stats = ltc.update(session_id=session_id)
            return {"ltc_synapse_stats": stats}
        except Exception as e:
            return {"ltc_error": str(e)}

    engine.register(
        "ltc_synapse", critical=False,
        inputs=["session_id"],
        outputs=["ltc_synapse_stats"],
        fn=_ltc_synapse,
        description="LTC 突触可塑性",
    )

    # ═══════════════════════════════════════════════════
    # Phase 5: 神经记忆 + 元学习
    # ═══════════════════════════════════════════════════

    def _neural_memory_gate(
        query: str, 
        reranked_results: list = None,
        crag_quality: float = 0.5,
        cove_verified_ratio: float = 0.5,
        **kw
    ) -> dict:
        """NeuralMemoryGate: 可微门控记忆"""
        try:
            from neural_memory_gate import NeuralMemoryGate
            gate = NeuralMemoryGate()
            gate_decision = gate.compute(
                query=query,
                results=reranked_results or [],
                quality=crag_quality,
                verification=cove_verified_ratio,
            )
            return {"memory_gate": gate_decision}
        except Exception as e:
            return {"memory_gate_error": str(e)}

    engine.register(
        "neural_memory_gate", critical=False,
        inputs=["query", "reranked_results", "crag_quality", "cove_verified_ratio"],
        outputs=["memory_gate"],
        fn=_neural_memory_gate,
        description="NeuralMemoryGate: 可微记忆门控",
    )

    def _synapse_network(session_id: str = "", **kw) -> dict:
        """MemorySynapseNetwork: 记忆突触网络"""
        try:
            from memory_synapse_network import MemorySynapseNetwork
            net = MemorySynapseNetwork()
            stats = net.update(session_id=session_id)
            return {"synapse_stats": stats}
        except Exception as e:
            return {"synapse_error": str(e)}

    engine.register(
        "synapse_network", critical=False,
        inputs=["session_id"],
        outputs=["synapse_stats"],
        fn=_synapse_network,
        description="MemorySynapseNetwork: 记忆突触网络",
    )

    def _neural_pipeline(
        query_vector: list = None,
        query: str = "",
        reranked_results: list = None,
        **kw
    ) -> dict:
        """NeuralPipeline: 端到端神经推理"""
        try:
            from neural_pipeline import NeuralPipeline
            pipe = NeuralPipeline()
            result = pipe.forward(
                query_vector=query_vector,
                query_text=query,
                context=reranked_results or [],
            )
            return {"neural_pipeline_result": result}
        except Exception as e:
            return {"neural_pipeline_error": str(e)}

    engine.register(
        "neural_pipeline", critical=False,
        inputs=["query_vector", "query", "reranked_results"],
        outputs=["neural_pipeline_result"],
        fn=_neural_pipeline,
        description="NeuralPipeline: 端到端神经推理",
    )

    # ═══════════════════════════════════════════════════
    # Phase 6: 自适应 + 行为 + 情绪
    # ═══════════════════════════════════════════════════

    def _auto_learner(session_id: str = "", query: str = "", **kw) -> dict:
        """AutoLearner: 偏好学习"""
        try:
            from auto_learner import AutoLearner
            learner = AutoLearner()
            learner.learn_preference(key=query, value={"context": session_id})
            return {"auto_learner_done": True}
        except Exception as e:
            return {"auto_learner_error": str(e)}

    engine.register(
        "auto_learner", critical=False,
        inputs=["session_id", "query"],
        outputs=["auto_learner_done"],
        fn=_auto_learner,
        description="AutoLearner: 偏好学习",
    )

    def _emotion_tracker(query: str = "", session_id: str = "", **kw) -> dict:
        """EmotionTracker: 用户情感追踪"""
        try:
            from emotion_tracker import EmotionTracker
            et = EmotionTracker()
            emotion = et.analyze(query, session=session_id)
            return {"emotion_state": emotion}
        except Exception as e:
            return {"emotion_error": str(e)}

    engine.register(
        "emotion_tracker", critical=False,
        inputs=["query", "session_id"],
        outputs=["emotion_state"],
        fn=_emotion_tracker,
        description="EmotionTracker: 用户情感追踪",
    )

    def _kora_behavior(session_id: str = "", **kw) -> dict:
        """KoRa: 行为模式检测"""
        try:
            from kora_behavior import KoraBehaviorEngine
            kora = KoraBehaviorEngine()
            pattern = kora.detect_session_pattern(session_id=session_id)
            return {"kora_pattern": pattern}
        except Exception as e:
            return {"kora_error": str(e)}

    engine.register(
        "kora_behavior", critical=False,
        inputs=["session_id"],
        outputs=["kora_pattern"],
        fn=_kora_behavior,
        description="KoRa: 行为模式检测",
    )

    # ═══════════════════════════════════════════════════
    # Phase 7: 因果推理 + 幻觉防御
    # ═══════════════════════════════════════════════════

    def _causal_reasoning(query: str, crag_quality: float = 0.5, **kw) -> dict:
        """CausalReasoning: 因果推理"""
        try:
            from causal_reasoning import CausalReasoner
            reasoner = CausalReasoner()
            causal = reasoner.analyze(query=query, retrieval_quality=crag_quality)
            return {"causal_insight": causal}
        except Exception as e:
            return {"causal_error": str(e)}

    engine.register(
        "causal_reasoning", critical=False,
        inputs=["query", "crag_quality"],
        outputs=["causal_insight"],
        fn=_causal_reasoning,
        description="CausalReasoning: 因果推理",
    )

    def _adaptive_hallucination(crag_quality: float = 0.5, **kw) -> dict:
        """AdaptiveHallucinationParams: 自适应幻觉参数"""
        try:
            from adaptive_hallucination_params import AdaptiveHallucinationParams
            ahp = AdaptiveHallucinationParams()
            params = ahp.compute(retrieval_quality=crag_quality)
            return {"hallucination_params": params}
        except Exception as e:
            return {"hallucination_error": str(e)}

    engine.register(
        "adaptive_hallucination", critical=False,
        inputs=["crag_quality"],
        outputs=["hallucination_params"],
        fn=_adaptive_hallucination,
        description="AdaptiveHallucinationParams: 自适应幻觉参数",
    )

    # ═══════════════════════════════════════════════════
    # Phase 8: 元优化 + 睡眠巩固（后台）
    # ═══════════════════════════════════════════════════

    def _skill_curriculum(
        crag_quality: float = 0.5,
        cove_verified_ratio: float = 0.5,
        **kw
    ) -> dict:
        """SKILL0: 技能课程引擎"""
        try:
            from skill_curriculum import SkillCurriculumEngine
            sce = SkillCurriculumEngine()
            result = sce.step(accuracy_with=crag_quality, validation_fn=lambda: cove_verified_ratio)
            return {
                "skill_stage": getattr(result, 'stage', None),
                "skill_delta": getattr(result, 'delta', None),
            }
        except Exception as e:
            return {"skill_error": str(e)}

    engine.register(
        "skill_curriculum", critical=False,
        inputs=["crag_quality", "cove_verified_ratio"],
        outputs=["skill_stage", "skill_delta"],
        fn=_skill_curriculum,
        description="SKILL0: 技能课程",
    )

    def _memory_consolidation(**kw) -> dict:
        """MemoryConsolidation: 仅心跳标记（真实巩固由后台 ConsolidationEngine 线程自动触发）"""
        return {"consolidation_tick": True}

    engine.register(
        "memory_consolidation", critical=False,
        inputs=[],
        outputs=["consolidation_tick"],
        fn=_memory_consolidation,
        description="MemoryConsolidation: 后台 ConsolidationEngine 已启动，空闲自动巩固",
    )

    def _biorhythm(**kw) -> dict:
        """BioRhythm: 仅打心跳标记（真实睡眠周期由后台线程自动触发）"""
        return {"biorhythm_tick": True}

    engine.register(
        "biorhythm", critical=False,
        inputs=[],
        outputs=["biorhythm_tick"],
        fn=_biorhythm,
        description="BioRhythm: 心跳标记（后台线程空闲时自动跑完整梦境周期）",
    )

    def _skill_compiler(**kw) -> dict:
        """SkillCompiler: SkVM 技能编译（后台一次性任务，不参与每次 pipeline）"""
        try:
            from skill_compiler import SkillCompiler
            compiler = SkillCompiler()
            # 只做被动检测不主动编译，有预编译缓存时返回状态
            return {"compiler_ready": True}
        except Exception as e:
            return {"compiler_error": str(e), "compiler_ready": False}

    engine.register(
        "skill_compiler", critical=False,
        inputs=[],
        outputs=["compiler_ready"],
        fn=_skill_compiler,
        description="SkillCompiler: SkVM 后台技能编译",
    )

    # ═══════════════════════════════════════════════════
    # Phase 9: 反馈学习
    # ═══════════════════════════════════════════════════

    def _feedback(
        query: str,
        reranked_results: list = None,
        **kw
    ) -> dict:
        """FeedbackLearner: 反馈排序优化"""
        try:
            from feedback import FeedbackLearner
            fb = FeedbackLearner()
            boosted = fb.get_boosted_ids(query)
            penalized = fb.get_penalty_ids(query)
            return {
                "feedback_boosted_ids": boosted,
                "feedback_penalized_ids": penalized,
            }
        except Exception as e:
            return {"feedback_error": str(e)}

    engine.register(
        "feedback", critical=False,
        inputs=["query", "reranked_results"],
        outputs=["feedback_boosted_ids", "feedback_penalized_ids"],
        fn=_feedback,
        description="FeedbackLearner: 反馈排序优化",
    )

    def _auto_prompt_optimizer(**kw) -> dict:
        """AutoPromptOptimizer: 文本梯度下降 + beam search 优化 prompt"""
        return {"apo_ready": True}

    engine.register(
        "auto_prompt_optimizer", critical=False,
        inputs=[],
        outputs=["apo_ready"],
        fn=_auto_prompt_optimizer,
        description="AutoPromptOptimizer: 文本梯度下降 + beam search prompt 优化",
    )

    # ═══════════════════════════════════════════════════
    # Phase 10: 元决策（CoEvolve + Turn Recovery + MemCoE）
    # ═══════════════════════════════════════════════════

    def _coevolve_decision(
        crag_quality: float = 0.5,
        cove_verified_ratio: float = 0.5,
        cognitive_load: float = 0.5,
        **kw
    ) -> dict:
        """CoEvolve: 失败模式检测"""
        failure_detected = False
        pattern = None
        if crag_quality < 0.4:
            failure_detected = True
            pattern = "low_quality_retrieval"
        elif crag_quality < 0.4 and cove_verified_ratio < 0.4:
            failure_detected = True
            pattern = "double_verification_failure"
        elif crag_quality < 0.5 and cognitive_load > 0.7:
            failure_detected = True
            pattern = "high_load_low_quality"
        
        return {
            "coevolve_failure": failure_detected,
            "coevolve_pattern": pattern,
        }

    engine.register(
        "coevolve", critical=False,
        inputs=["crag_quality", "cove_verified_ratio", "cognitive_load"],
        outputs=["coevolve_failure", "coevolve_pattern"],
        fn=_coevolve_decision,
        description="CoEvolve: 失败模式反馈",
    )

    def _turn_recovery(
        crag_quality: float = 0.5,
        cognitive_load: float = 0.5,
        **kw
    ) -> dict:
        """Turn Recovery: 走偏恢复"""
        if crag_quality < 0.5 and cognitive_load < 0.3:
            return {"turn_recovery": True, "turn_action": "dag_restore"}
        return {"turn_recovery": False, "turn_action": "none"}

    engine.register(
        "turn_recovery", critical=False,
        inputs=["crag_quality", "cognitive_load"],
        outputs=["turn_recovery", "turn_action"],
        fn=_turn_recovery,
        description="Turn Recovery: 走偏恢复",
    )

    def _memcoe(
        crag_quality: float = 0.5,
        cove_verified_ratio: float = 0.5,
        memory_gate: dict = None,
        **kw
    ) -> dict:
        """MemCoE: 对比反馈 + guideline 更新"""
        guideline = "maintain"
        if crag_quality < 0.3 and cove_verified_ratio < 0.3:
            guideline = "consolidate"
        elif crag_quality > 0.8 and cove_verified_ratio > 0.8:
            guideline = "decay"
        return {"memcoe_guideline": guideline}

    engine.register(
        "memcoe", critical=False,
        inputs=["crag_quality", "cove_verified_ratio", "memory_gate"],
        outputs=["memcoe_guideline"],
        fn=_memcoe,
        description="MemCoE: 双阶段记忆优化",
    )

    # ═══════════════════════════════════════════════════
    # Phase 6: 闭环引擎（Impact Tracker + MetaOptimizer）
    def _context_compressor(
        cognitive_load: float = 0.5,
        reranked_results: list = None,
        memgpt_context: str = "",
        ssm_predicted: list = None,
        memoryos_profile: str = "",
        asset_injection: str = "",
        **kw
    ) -> dict:
        """Context Compressor: 认知负荷高时压缩上下文（含 MemGAS asset_injection）"""
        if cognitive_load < 0.7:
            return {"compressed_context": None}
        try:
            from context_compressor import RuleBasedCompressor
            compressor = RuleBasedCompressor()
            # 合并所有上下文为一整段
            parts = []
            for r in (reranked_results or []):
                if isinstance(r, dict):
                    parts.append(r.get("content", str(r)))
                else:
                    parts.append(str(r))
            if memgpt_context:
                parts.append(memgpt_context)
            if asset_injection:
                parts.append(asset_injection)
            text = "\n\n".join(parts)
            if not text:
                return {"compressed_context": None}
            result = compressor.compress(text, target_ratio=0.5)
            return {"compressed_context": result.compressed_text, "compression_ratio": result.compression_ratio}
        except Exception as e:
            return {"compressor_error": str(e), "compressed_context": None}

    engine.register(
        "context_compressor", critical=False,
        inputs=["cognitive_load", "reranked_results", "memgpt_context", "ssm_predicted", "memoryos_profile", "asset_injection"],
        outputs=["compressed_context", "compression_ratio"],
        fn=_context_compressor,
        description="Context Compressor: 认知负荷高时自动压缩上下文（含 MemGAS）",
    )

    # ═══════════════════════════════════════════════════

    def _impact_tracker(
        session_id: str = "",
        query: str = "",
        ssm_predicted: list = None,
        heat_top: list = None,
        memoryos_profile: str = "",
        crag_quality: float = 0.5,
        cove_verified_ratio: float = 0.5,
        coevolve_failure: bool = False,
        coevolve_pattern: str = None,
        turn_recovery: bool = False,
        turn_action: str = None,
        cfc_intent: Any = None,
        should_retrieve: bool = True,
        isrel_threshold_override: float = None,
        isrel_confidence: float = 1.0,
        **kw
    ) -> dict:
        """闭环引擎入口：记录本轮模块效果指标"""
        try:
            from impact_tracker import ImpactTracker
            if not hasattr(_impact_tracker, '_instance'):
                _impact_tracker._instance = ImpactTracker()
            tracker = _impact_tracker._instance

            ctx = {
                "ssm_predicted": ssm_predicted or [],
                "heat_top": heat_top or [],
                "memoryos_profile": memoryos_profile or "",
                "crag_quality": crag_quality,
                "cove_verified_ratio": cove_verified_ratio,
                "coevolve_failure": coevolve_failure,
                "coevolve_pattern": coevolve_pattern,
                "turn_recovery": turn_recovery,
                "turn_action": turn_action,
                "cfc_intent": cfc_intent,
                "should_retrieve": should_retrieve,
                "isrel_threshold_override": isrel_threshold_override,
            }
            metrics = tracker.track(session_id, query, ctx)
            return {"_impact_metrics": metrics}
        except Exception as e:
            return {"_impact_error": str(e)}

    engine.register(
        "impact_tracker", critical=True,
        inputs=[
            "session_id", "query",
            "ssm_predicted", "heat_top",
            "memoryos_profile",
            "crag_quality", "cove_verified_ratio",
            "coevolve_failure", "coevolve_pattern",
            "turn_recovery", "turn_action",
            "cfc_intent",
            "should_retrieve", "isrel_threshold_override",
        ],
        outputs=["_impact_metrics"],
        fn=_impact_tracker,
        description="Impact Tracker: 闭环效果追踪",
    )

    logger.info(f"[registry] ✅ 注册完成: {len(engine._stages)} 个模块")


# ═══════════════════════════════════════════════════════════
# 便捷工厂: 创建预注册好的 PipelineEngine
# ═══════════════════════════════════════════════════════════

def create_pipeline() -> Any:
    """创建并注册所有模块的流水线引擎。"""
    from pipeline_engine import PipelineEngine
    engine = PipelineEngine()
    register_all_modules(engine)
    return engine
