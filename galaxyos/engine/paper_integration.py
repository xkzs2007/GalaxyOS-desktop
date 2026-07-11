#!/usr/bin/env python3
"""
10( + 1) Paper Directions Integration Layer + LASAR Cognitive Map

一次性整合所有论文方向到 R-CCAM。
由 XiaoyiClawLLM 的 R-CCAM 方法调用, 不对现有逻辑造成侵入性影响。

第 11 个: LASAR Latent Cognitive Map (arXiv:2605.16899)
"""

import json
import os
import time
import logging
import re
from typing import Dict, List, Optional, Any
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)

# ── lazyload: 只在调用时导入, 不影响系统启动 ──
_M = {}  # 模块缓存


def _lazy(mod_name: str):
    """Lazy import 单例化"""
    if mod_name not in _M:
        _M[mod_name] = __import__(mod_name)
    return _M[mod_name]


class PaperIntegration:
    """10论文集成层 + 时序知识图谱 + LASAR Cognitive Map — 统一入口, R-CCAM 各阶段调用"""

    def __init__(self, llm_flash=None, workspace: str = "", db_path: str = None):
        self.flash = llm_flash
        self.ws = workspace or os.environ.get(
            "WORKSPACE", workspace())
        self.db_path = db_path or os.path.join(self.ws, 'temporal_kg.db')
        # ── 预加载: R-CCAM 全部模块, 消除首次调用延迟 ──
        self._preload_modules()

    def _preload_modules(self):
        """预加载 10 个论文模块 + 3 个增强模块, R-CCAM 启动时全部就绪"""
        self._se = None
        self._cr = None
        self._cl = None
        self._et = None
        self._hr = None
        self._ps = None
        self._me = None
        self._tkg = None
        self._cm = None
        self._sg = None
        self._ac = None
        self._tot = None

        from semantic_entropy import SemanticEntropy
        from causal_reasoning import CausalReasoning
        from cognitive_load import CognitiveLoad
        from emotion_tracker import EmotionTracker
        from hyper_routing import HyperRouter
        from plan_solve import PlanSolve
        from memory_editor import MemoryEditor
        from temporal_kg import TemporalKnowledgeGraph
        from cognitive_map import CognitiveMap
        from spatial_topology import SpatialTopologyGraph

        try:
            self._se = SemanticEntropy(self.flash)
        except Exception as e:
            logger.warning(f"SemanticEntropy 预加载失败: {e}")
        try:
            self._cr = CausalReasoning(self.flash)
        except Exception as e:
            logger.warning(f"CausalReasoning 预加载失败: {e}")
        try:
            self._cl = CognitiveLoad()
        except Exception as e:
            logger.warning(f"CognitiveLoad 预加载失败: {e}")
        try:
            from time import time
            self._et = EmotionTracker(os.path.join(self.ws, ".learnings", "emotion_track.json"))
        except Exception as e:
            logger.warning(f"EmotionTracker 预加载失败: {e}")
        try:
            self._hr = HyperRouter(os.path.join(self.ws, ".learnings", "hyper_router.json"))
        except Exception as e:
            logger.warning(f"HyperRouter 预加载失败: {e}")
        try:
            self._ps = PlanSolve(self.flash)
        except Exception as e:
            logger.warning(f"PlanSolve 预加载失败: {e}")
        try:
            self._me = MemoryEditor(self.flash)
        except Exception as e:
            logger.warning(f"MemoryEditor 预加载失败: {e}")
        tkg_db = os.path.join(os.path.dirname(self.db_path or ''), 'temporal_kg.db') if self.db_path else None
        try:
            self._tkg = TemporalKnowledgeGraph(db_path=tkg_db)
        except Exception as e:
            logger.warning(f"TemporalKG 预加载失败: {e}")
        try:
            cm_db = os.path.join(os.path.dirname(self.db_path or ''), 'cognitive_map.db') if self.db_path else None
            self._cm = CognitiveMap(db_path=cm_db)
        except Exception as e:
            logger.warning(f"CognitiveMap 预加载失败: {e}")
        try:
            db_dir = os.path.dirname(self.db_path) if self.db_path else os.path.join(self.ws, '.learnings')
            os.makedirs(db_dir, exist_ok=True)
            self._sg = SpatialTopologyGraph(db_path=os.path.join(db_dir, 'spatial_topology.db'))
        except Exception as e:
            logger.warning(f"SpatialTopology 预加载失败: {e}")
            self._sg = False
        try:
            from adaptive_classifier import AdaptiveClassifier
            self._ac = AdaptiveClassifier()
        except Exception as e:
            logger.warning(f"AdaptiveClassifier 预加载失败: {e}")
            self._ac = None
        try:
            from tree_of_thought import TreeOfThought
            self._tot = TreeOfThought(self.flash)
        except Exception as e:
            logger.warning(f"TreeOfThought 预加载失败: {e}")
            self._tot = None

        logger.info(f"PaperIntegration 预加载完成: SE={self._se is not None} CR={self._cr is not None} "
                     f"CL={self._cl is not None} ET={self._et is not None} HR={self._hr is not None} "
                     f"PS={self._ps is not None} ME={self._me is not None} TKG={self._tkg is not None} "
                     f"CM={self._cm is not None} SG={self._sg is not None} AC={self._ac is not None} TOT={self._tot is not None}")

    # ────────── Phase 0: 规划前置 (Plan-and-Solve) ──────────

    def pre_plan(self, query: str) -> Dict:
        """Plan-and-Solve: 制定执行计划"""
        if not self.flash:
            return {"plan": [], "has_plan": False}
        ps = self._get_plan_solve()
        return ps.execute(query)

    # ────────── Phase 1: 检索前决策 (Semantic Entropy + Adaptive-RAG) ──────────

    def assess_uncertainty(self, query: str) -> Dict:
        """语义熵: 判断是否需要检索"""
        se = self._get_semantic_entropy()
        return se.measure(query)

    def get_search_strategy(self, query: str, semantic_entropy: float = 0.5,
                            is_followup: bool = False) -> Dict:
        """HyperRouter + Adaptive-RAG: 选择检索策略"""
        hr = self._get_hyper_router()
        from hyper_routing import extract_features
        features = extract_features(query, semantic_entropy, is_followup)
        return hr.select_strategy(features)

    def provide_search_feedback(self, strategy: str, success: bool,
                                 latency_ms: float = 0):
        """反馈学习: 更新路由策略"""
        hr = self._get_hyper_router()
        return hr.feedback(strategy, success, latency_ms)

    # ────────── Phase 2: 认知增强 (Causal CoT + Emotion) ──────────

    def inject_causal_context(self, query: str, user_response: str = "") -> Dict:
        """因果推理: 注入因果图到 prompt"""
        cr = self._get_causal_reasoning()
        text = f"用户: {query}\nAI回复: {user_response}" if user_response else query
        return cr.analyze(text)

    def inject_emotion_context(self, text: str = "", session: str = "") -> str:
        """情感轨迹: 生成情感上下文注入文本"""
        et = self._get_emotion_tracker()
        if text:
            et.update(text, session)
        return et.inject_to_context()

    def update_emotion(self, text: str, session: str = ""):
        """记录情感"""
        et = self._get_emotion_tracker()
        et.update(text, session)

    # ────────── Phase 3: 路由决策 (Hypernetwork) ──────────

    def decide_routing(self, query: str, semantic_entropy: float,
                       is_followup: bool = False) -> str:
        """决定走什么路由策略"""
        strategy = self.get_search_strategy(query, semantic_entropy, is_followup)
        return strategy.get("name", "quick_recall")

    # ────────── Phase 3.5: 认知负荷评估 (Cognitive Load) ──────────

    def assess_cognitive_load(self, query: str, nodes: List = None,
                                session_history: List = None) -> Dict:
        """认知负荷评估: 给 DAG 压缩决策建议"""
        cl = self._get_cognitive_load()
        return cl.assess(query, nodes or [], session_history or [])

    # ────────── Phase 4: 记忆修正 (Self-Correcting Memory) ──────────

    def amend_on_conflict(self, old_id: str, old_content: str,
                          new_content: str) -> Dict:
        """ROME 风格记忆修正"""
        me = self._get_memory_editor()
        return me.amend_memory(old_id, old_content, new_content)

    def merge_redundant(self, memories: List[Dict]) -> List[Dict]:
        """Generative Replay 风格合并"""
        me = self._get_memory_editor()
        return me.merge_similar(memories)

    # ────────── 时序知识图谱集成 ──────────

    def _get_temporal_kg(self):
        """懒加载 TemporalKnowledgeGraph"""
        if self._tkg is None:
            from temporal_kg import TemporalKnowledgeGraph
            db_path = os.path.join(os.path.dirname(self.db_path or ''), 'temporal_kg.db') if self.db_path else None
            self._tkg = TemporalKnowledgeGraph(db_path=db_path)
        return self._tkg

    def extract_and_store_entities(self, text: str, timestamp: float = None,
                                    session_key: str = None) -> dict:
        """
        从文本抽实体 → 存到时序KG。

        Args:
            text: 用户输入文本
            timestamp: 事实发生时间（None=当前）
            session_key: 会话标识

        Returns:
            {
                "entities_found": int,
                "edges_created": int,
                "conflicts_resolved": int,
                "summary": str
            }
        """
        tkg = self._get_temporal_kg()
        ts = timestamp or time.time()
        extractions = tkg.extract_entities_from_text(text, llm=self.flash)
        if not extractions:
            return {"entities_found": 0, "edges_created": 0, "conflicts_resolved": 0, "summary": "未抽取到实体"}

        entities_found = 0
        edges_created = 0
        conflicts_resolved = 0
        entity_map = {}

        # 所有抽取到的实体列表
        all_entities = []

        for ext in extractions:
            ent_name = ext.get('entity', '').strip()
            ent_type = ext.get('type', 'unknown')
            target_name = ext.get('target', '').strip()
            relation = ext.get('relation', '').strip()
            if not ent_name:
                continue
            # 先尝试创建实体（带类型），如果已存在则 disambiguate
            existing_ent = tkg.get_entity(ent_name, fuzzy=False)
            if existing_ent:
                eid = tkg.disambiguate_entity(ent_name, text)
            else:
                eid = tkg.add_entity(ent_name, ent_type)
            entity_map[ent_name] = eid
            entities_found += 1
            all_entities.append((ent_name, eid, ent_type))

            if target_name and relation:
                existing_target = tkg.get_entity(target_name, fuzzy=False)
                if existing_target:
                    tid = tkg.disambiguate_entity(target_name, text)
                else:
                    tid = tkg.add_entity(target_name, 'unknown')
                entity_map[target_name] = tid
                existing = tkg.get_active_edges(entity=ent_name) if hasattr(tkg, 'get_active_edges') else []
                if existing:
                    conflict_result = tkg.detect_and_resolve_conflict(
                        f"{ent_name} {relation} {target_name}: {text[:200]}",
                        existing, llm=self.flash)
                    if conflict_result.get('conflict_detected'):
                        conflicts_resolved += len(conflict_result.get('edges_to_invalidate', []))
                edge_id = tkg.add_temporal_edge(
                    ent_name, target_name, relation,
                    timestamp=ts, content=text[:500], session_key=session_key or '')
                if edge_id:
                    edges_created += 1

        # 兜底: 同段文本的实体间建"共现"边（确保每轮对话都有关系落地）
        if len(all_entities) >= 2 and edges_created == 0:
            for i in range(len(all_entities)):
                for j in range(i + 1, len(all_entities)):
                    e1, _, _ = all_entities[i]
                    e2, _, _ = all_entities[j]
                    edge_id = tkg.add_temporal_edge(
                        e1, e2, '共现',
                        timestamp=ts, content=text[:300],
                        session_key=session_key or '')
                    if edge_id:
                        edges_created += 1

        summary = f"抽取 {entities_found} 个实体, 创建 {edges_created} 条关系, 解决 {conflicts_resolved} 个冲突"
        logger.info(f"TKG extract_and_store: {summary}")
        return {"entities_found": entities_found, "edges_created": edges_created,
                "conflicts_resolved": conflicts_resolved, "summary": summary,
                "entity_map": entity_map}

    def temporal_retrieve(self, query: str, current_time: float = None,
                          session_key: str = "") -> list:
        """时间感知的混合检索 (v7.1: session_key 过滤)"""
        tkg = self._get_temporal_kg()
        ts = current_time or time.time()
        results = tkg.hybrid_retrieve(query, at_time=ts, top_k=10,
                                      session_key=session_key)
        formatted = []
        for r in results:
            formatted.append({
                "id": r.get('edge_id', ''),
                "content": f"{r['src_entity']} -[{r['relation']}]-> {r['dst_entity']}: {r.get('content', '')}",
                "source": "temporal_kg", "score": r.get('score', 0),
                "metadata": {"t_created": r.get('t_created', 0), "relation": r.get('relation', ''),
                             "session_key": r.get('session_key', '')}
            })
        return formatted

    def get_session_community_summary(self, session_key: str) -> str:
        """生成会话级社区摘要。"""
        tkg = self._get_temporal_kg()
        session_graph = tkg.get_session_graph(session_key)
        if session_graph['stats']['edge_count'] < 2:
            return "会话中实体关系较少，暂不生成社区摘要"
        communities = tkg.build_community(min_edges=2)
        if not communities:
            return "会话数据不足以形成社区"
        parts = []
        for comm in communities[:3]:
            members = comm.get('members', [])
            centroid = comm.get('centroid', [])[:5]
            cid = comm.get('community_id', '?')[:12]
            parts.append(f"[社区 {cid}] 成员({len(members)}人): {', '.join(members[:5])}")
            if centroid:
                parts.append(f"  关键词: {', '.join(centroid)}")
        return "\n".join(parts)

    def invalidate_conflicting_edges(self, new_content: str) -> dict:
        """检测新内容与KG的矛盾，自动invalidate。"""
        tkg = self._get_temporal_kg()
        active_edges = tkg.get_active_edges()
        result = tkg.detect_and_resolve_conflict(new_content, active_edges, llm=self.flash)
        return {"conflicts_found": 1 if result.get('conflict_detected') else 0,
                "edges_invalidated": len(result.get('edges_to_invalidate', [])),
                "details": [result.get('reasoning', '')] if result.get('conflict_detected') else []}

    def temporal_augment_context(self, raw_nodes: list) -> list:
        """给 DAG 节点附加时序信息。"""
        tkg = self._get_temporal_kg()
        enhanced = []
        for node in raw_nodes:
            content = node.get('content', '')
            if len(content) < 10:
                enhanced.append(node)
                continue
            words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', content))
            matched_entities = []
            matched_relations = []
            active_edges = tkg.get_active_edges()
            for e in active_edges[:20]:
                src_name = e.get('src_entity', '')
                dst_name = e.get('dst_entity', '')
                if src_name in words or dst_name in words:
                    matched_entities.append(src_name)
                    matched_entities.append(dst_name)
                    matched_relations.append(f"{src_name} -[{e.get('relation', '?')}]-> {dst_name}")
            if matched_entities or matched_relations:
                node = dict(node)
                temporal_info = {
                    "tkg_matched_entities": list(set(matched_entities))[:5],
                    "tkg_relations": matched_relations[:3],
                    "tkg_edge_count": len(matched_relations),
                }
                if 'metadata' not in node or node['metadata'] is None:
                    node['metadata'] = {}
                elif isinstance(node['metadata'], str):
                    try:
                        node['metadata'] = json.loads(node['metadata'])
                    except (json.JSONDecodeError, TypeError):
                        node['metadata'] = {}
                node['metadata']['temporal_info'] = temporal_info
            enhanced.append(node)
        return enhanced

    # ────────── LASAR Cognitive Map 集成 ──────────

    def _get_cognitive_map(self):
        """懒加载 LASAR CognitiveMap"""
        if self._cm is None:
            from cognitive_map import CognitiveMap
            db_path = os.path.join(
                os.path.dirname(self.db_path or ''), 'cognitive_map.db') if self.db_path else None
            self._cm = CognitiveMap(db_path=db_path)
        return self._cm

    def create_anchor_for_node(self, node_id: str, content: str,
                                session_key: str = "") -> str:
        """为 DAG 节点创建认知锚点"""
        cm = self._get_cognitive_map()
        return cm.add_anchor(node_id, content[:500], session_key)

    def proximity_rerank(self, results: List[Dict],
                          current_context: str,
                          session_key: str = "") -> List[Dict]:
        """用空间接近性重排序检索结果 (v7.1: session_key)"""
        cm = self._get_cognitive_map()
        if not results:
            return results
        vec = cm.compute_anchor_vector(current_context, session_key=session_key)
        scored = []
        for r in results:
            content = r.get("content", r.get("context", ""))
            r_vec = cm.compute_anchor_vector(content[:200])
            prox = cm.spatial_similarity(vec, r_vec)
            scored.append((prox, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    # ────────── AriGraph 空间拓扑（AriGraph: arXiv:2407.04363）──────────

    def extract_and_register_scene(self, text: str, current_session: str = None) -> str:
        """
        从对话文本识别场景关系 -> 注册到空间拓扑图。
        从文本中抽取场景标签，自动建立层级（父/子场景）。
        """
        sg = self._get_spatial_graph()
        if not sg:
            return ""

        scene_label = ""
        parent_scene = None

        if current_session and '/' in current_session and all(c.isalnum() or c in '/_-' for c in current_session):
            parts = current_session.split('/')
            scene_label = parts[-1].strip()
            if len(parts) >= 2:
                parent_scene = parts[-2].strip()

        if not scene_label:
            m = re.search(r'([一-鿿A-Za-z0-9]{2,12}(?:平台|系统|项目|框架|组件|工具|模块|技能|架构|方案))', text)
            if m:
                scene_label = m.group(1)

        if not scene_label:
            m = re.search(r'([A-Z][a-z]+[A-Z][a-zA-Z0-9]{2,20}|[A-Z]{3,8})', text)
            if m:
                scene_label = m.group(1)

        if not scene_label:
            m = re.search(r'(?:在|去|位于)\s*([\u4e00-\u9fff]{2,4}(?:市|区|县|省)?)', text)
            if m:
                scene_label = m.group(1)
                parent_scene = "地点"

        if not scene_label:
            cn_chars = ''.join(re.findall(r'[\u4e00-\u9fff]', text[:30]))
            scene_label = cn_chars[:8] if cn_chars else "general_discussion"
            parent_scene = None

        existing = sg.get_scene(scene_label)
        if existing:
            return scene_label

        if parent_scene:
            try:
                existing_parent = sg.get_scene(parent_scene)
                if not existing_parent:
                    sg.register_scene(label=parent_scene[:50], scene_type="context")
            except Exception:
                pass

        try:
            sg.register_scene(
                label=scene_label[:50],
                scene_type="context",
                parent_label=parent_scene[:50] if parent_scene else None,
                metadata={"source": "text_extraction", "session": current_session or ""},
            )
            logger.info(f"AriGraph scene: {scene_label} (parent={parent_scene})")
            return scene_label
        except Exception as e:
            logger.warning(f"extract_and_register_scene failed: {e}")
            return ""

    def spatial_augment_retrieval(self, query: str, current_context: str) -> list:
        """
        空间增强检索: 在当前场景附近找关联记忆。

        Returns:
            检索结果列表
        """
        sg = self._get_spatial_graph()
        if not sg or not current_context:
            return []
        try:
            return sg.spatial_retrieve(query=query, current_context=current_context, top_k=5)
        except Exception as e:
            logger.warning(f"spatial_augment_retrieval failed: {e}")
            return []

    def infer_current_scene(self, entities: list) -> str:
        """根据当前实体推断用户所在场景"""
        sg = self._get_spatial_graph()
        if not sg or not entities:
            return ""
        try:
            scene = sg.infer_scene_from_entities(entities)
            return scene or ""
        except Exception as e:
            logger.warning(f"infer_current_scene failed: {e}")
            return ""

    def get_scene_navigation_context(self, from_scene: str, to_scene: str) -> str:
        """生成场景导航上下文字段"""
        sg = self._get_spatial_graph()
        if not sg or not from_scene or not to_scene:
            return ""
        try:
            path = sg.get_navigation_path(from_scene, to_scene)
            if path:
                return f"你从 {from_scene} 转移到了 {to_scene} (路径: {' → '.join(path)})"
            return f"你从 {from_scene} 转向了 {to_scene}"
        except Exception as e:
            logger.warning(f"get_scene_navigation_context failed: {e}")
            return ""

    def spatial_rerank(self, results: list, current_context: str = None,
                       session_key: str = "") -> list:
        """空间重排序: 越接近当前场景的记忆排名越高 (v7.1: session_key 分区)"""
        if not current_context or not results:
            return results
        sg = self._get_spatial_graph()
        if not sg:
            return results
        try:
            current_node = sg.get_scene(current_context, session=session_key)
            if not current_node:
                return results

            # v7.1: 仅在当前 session 的场景图中搜索
            for r in results:
                r_session = r.get("metadata", {}).get("session_key", "")
                if session_key and r_session and r_session != session_key:
                    r["spatial_score"] = 0.0
                    continue
                content = r.get("content", "") or r.get("text", "")
                if not content:
                    r["spatial_score"] = 0.5
                    continue
                keywords = _extract_keywords_from_query(content)[:5]
                score = 0.0
                matched = 0
                for kw in keywords:
                    scene_node = sg.get_scene(kw, session=session_key)
                    if scene_node:
                        dist = sg._graph_distance(current_node.node_id, scene_node.node_id)
                        if dist == 0:
                            score += 1.0
                        elif dist <= 1:
                            score += 0.8
                        elif dist <= 2:
                            score += 0.5
                        elif dist <= 3:
                            score += 0.3
                        matched += 1
                r["spatial_score"] = score / max(matched, 1)
            for r in results:
                orig_score = r.get("score", 0.5)
                spatial = r.get("spatial_score", 0.5)
                r["score"] = orig_score * 0.7 + spatial * 0.3
            results.sort(key=lambda x: -x.get("score", 0))
            return results
        except Exception as e:
            logger.warning(f"spatial_rerank failed: {e}")
            return results

    def spatial_context_augment(self, query_context: str, entities: List[str]) -> dict:
        """给检索结果附加空间上下文"""
        sg = self._get_spatial_graph()
        if not sg:
            return {}
        try:
            return sg.spatial_context_augment(query_context, entities)
        except Exception as e:
            logger.warning(f"spatial_context_augment failed: {e}")
            return {}

    def generate_three_queries(self, current_context: str,
                                session_key: str = "") -> Dict:
        """生成三类认知 query 的答案

        LASAR: Retrospective(回顾) / Introspective(内省) / Prospective(预测)
        """
        cm = self._get_cognitive_map()
        return cm.run_cognitive_queries(current_context, session_key)

    def get_cognitive_context(self, current_context: str) -> str:
        """生成认知上下文注入文本

        格式: "你在一个[密集/稀疏]的认知区域，附近有[X]条相关记忆"
        """
        cm = self._get_cognitive_map()
        vec = cm.compute_anchor_vector(current_context)
        density = cm.get_anchor_density(vec)
        nearby = cm.get_nearby_anchors(vec, k=5)

        familiarity = (
            "熟悉的" if density > 0.5 else
            "较熟悉的" if density > 0.2 else
            "新的"
        )
        return (
            f"认知状态: 你在一个{familiarity}区域，"
            f"附近 {len(nearby)} 条相关记忆，"
            f"认知密度 {density:.2f}。"
        )

    def get_cognitive_map_stats(self) -> Dict:
        """获取认知地图统计"""
        cm = self._get_cognitive_map()
        return cm.get_stats()

    # ────────── 工具函数 ──────────

    def get_routing_stats(self) -> Dict:
        hr = self._get_hyper_router()
        return hr.get_stats()

    def get_emotion_state(self) -> Dict:
        et = self._get_emotion_tracker()
        return et.get_current_state()

    def get_emotion_trajectory(self, days: int = 7) -> Dict:
        et = self._get_emotion_tracker()
        return et.get_trajectory(days)

    # ────────── Lazy 初始化 ──────────

    def _get_semantic_entropy(self):
        if self._se is None:
            from semantic_entropy import SemanticEntropy
            self._se = SemanticEntropy(self.flash)
        return self._se

    def _get_causal_reasoning(self):
        if self._cr is None:
            from causal_reasoning import CausalReasoning
            self._cr = CausalReasoning(self.flash)
        return self._cr

    def _get_emotion_tracker(self):
        if self._et is None:
            from emotion_tracker import EmotionTracker
            self._et = EmotionTracker(os.path.join(self.ws, ".learnings", "emotion_track.json"))
        return self._et

    def _get_hyper_router(self):
        if self._hr is None:
            from hyper_routing import HyperRouter
            self._hr = HyperRouter(os.path.join(self.ws, ".learnings", "hyper_router.json"))
        return self._hr

    def _get_plan_solve(self):
        if self._ps is None:
            from plan_solve import PlanSolve
            self._ps = PlanSolve(self.flash)
        return self._ps

    def _get_memory_editor(self):
        if self._me is None:
            from memory_editor import MemoryEditor
            self._me = MemoryEditor(self.flash)
        return self._me

    def _get_cognitive_load(self):
        if self._cl is None:
            from cognitive_load import CognitiveLoad
            self._cl = CognitiveLoad()
        return self._cl

    def _get_spatial_graph(self):
        """懒加载 SpatialTopologyGraph"""
        if self._sg is None:
            try:
                from spatial_topology import SpatialTopologyGraph
                db_dir = os.path.dirname(self.db_path) if self.db_path else os.path.join(self.ws, '.learnings')
                os.makedirs(db_dir, exist_ok=True)
                spatial_db = os.path.join(db_dir, 'spatial_topology.db')
                self._sg = SpatialTopologyGraph(db_path=spatial_db)
            except Exception as e:
                logger.warning(f"SpatialTopologyGraph 初始化失败: {e}")
                self._sg = False
        return self._sg if self._sg else None


    # ────────── Adaptive-RAG + CRAG: 检索前分类 + 分解检索 ──────────

    def adaptive_classify(self, query: str) -> Dict:
        """Adaptive-RAG: 分类 query 复杂度, 决定检索策略"""
        ac = self._get_adaptive_classifier()
        return ac.classify(query)

    def adaptive_feedback(self, query: str, actual_level: str, success: bool):
        """Adaptive-RAG 反馈: 优化分类器"""
        ac = self._get_adaptive_classifier()
        ac.feedback(query, actual_level, success)

    def crag_decompose_and_search(self, query: str, top_k: int = 8) -> Dict:
        """CRAG: 复合查询分解→子查询分别检索→RRF重组"""
        from retrieval_hub import retrieval_hub, _decompose_query, _recompose_results
        sub_queries = _decompose_query(query)
        if len(sub_queries) <= 1:
            return {"used_crag": False, "results": []}
        sub_results = []
        for sq in sub_queries[:4]:
            try:
                hr = retrieval_hub(sq, top_k=top_k, include_web=True)
                sub_results.append(hr.get('results', []))
            except Exception:
                sub_results.append([])
        merged = _recompose_results(sub_results)[:top_k]
        return {"used_crag": True, "sub_queries": sub_queries, "results": merged}

    # ────────── Self-Correcting Memory (ROME + Generative Replay) ──────────

    def locate_and_amend(self, old_content: str, new_content: str) -> Dict:
        """ROME locate->amend 闭环: 找到旧记忆, 自动修正"""
        me = self._get_memory_editor()
        located = me.locate(old_content, new_content)
        if not located.get('located_id'):
            return {"ok": False, "error": "未找到匹配的旧记忆"}
        return me.amend_memory(
            located['located_id'], located['located_content'], new_content
        )

    def generative_replay(self, memories: List[Dict] = None) -> Dict:
        """Generative Replay: 低价值记忆摘要重写+重要性重算"""
        me = self._get_memory_editor()
        if not memories:
            return {"replayed": 0, "summary": "无输入记忆"}
        return me.generative_replay(memories)

    # ────────── Tree-of-Thought: 多路径探索 ──────────

    def multi_path_search(self, query: str, context: str = "") -> Dict:
        """ToT 多路径搜索: 复杂 query 生成多个推理分支, 评估后选最优"""
        tot = self._get_tree_of_thought()
        return tot.search(query, context)

    def get_thinking_trace(self) -> List[Dict]:
        """获取 ToT 探索记录"""
        tot = self._get_tree_of_thought()
        return tot.get_thinking_trace()

    # ────────── Cognitive Load 压缩建议 ──────────

    def get_compression_advice(self, query: str, dag_nodes: List = None,
                                 history: List = None) -> Dict:
        """认知负荷驱动的 DAG 压缩建议"""
        cl = self._get_cognitive_load()
        return cl.get_compression_advice(query, dag_nodes or [], history or [])

    # ────────── Emotion Weighted Search ──────────

    def emotion_weighted_rerank(self, query_results: List[Dict],
                                   current_text: str = "") -> List[Dict]:
        """情感权重检索重排序"""
        et = self._get_emotion_tracker()
        current_emotion = et.get_current_state()
        return et.emotion_weighted_search(query_results, current_emotion)

    # ────────── Causal Graph ──────────

    def get_causal_graph(self) -> Dict:
        """返回当前因果图"""
        cr = self._get_causal_reasoning()
        return cr.get_causal_graph()

    def get_causal_chains(self, from_var: str, to_var: str) -> List:
        """两变量间所有因果路径"""
        cr = self._get_causal_reasoning()
        return cr.get_causal_chains(from_var, to_var)

    # ────────── Lazy 初始化: 新模块 ──────────

    def _get_adaptive_classifier(self):
        if self._ac is None:
            from adaptive_classifier import AdaptiveClassifier
            self._ac = AdaptiveClassifier()
        return self._ac

    def _get_tree_of_thought(self):
        if self._tot is None:
            from tree_of_thought import TreeOfThought
            self._tot = TreeOfThought(self.flash)
        return self._tot




# ── 全局单例 ──
_instance = None


def get_integration(llm_flash=None, workspace: str = "") -> PaperIntegration:
    global _instance
    if _instance is None:
        _instance = PaperIntegration(llm_flash, workspace)
    elif llm_flash and _instance.flash is None:
        _instance.flash = llm_flash
    return _instance


def _extract_keywords_from_query(text: str) -> list:
    """从文本提取关键词（辅助函数，避免循环导入）"""
    chinese = re.findall(r'[\u4e00-\u9fff]{2,}', text)
    english = re.findall(r'[a-zA-Z]{3,}', text)
    return chinese + english
