#!/usr/bin/env python3
"""
小艺 Claw 大模型 - 统一 API

提供统一的记忆管理接口,整合所有底层能力。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
import uuid
import re
import time as _rccam_tm

logger = logging.getLogger(__name__)

# ── 工作区路径(确保整个模块统一可访问)─────────────────────────
WORKSPACE = os.environ.get(
    "OPENCLAW_WORKSPACE",
    str(Path.home() / ".openclaw" / "workspace"),
)

# ── Yaoyao Bridge(三路融合用)──────────────────────────────────
_yaoyao_bridge = None

def _get_yaoyao_bridge():
    """Lazy-load yaoyao_bridge(失败不影响主流程)"""
    global _yaoyao_bridge
    if _yaoyao_bridge is not None:
        return _yaoyao_bridge
    try:
        import sys as _sys
        # xiaoyi_claw_api.py 在 .../core/ 下,需要跳到 workspace/scripts/
        # 用绝对路径避免 __file__ 相对路径计算偏差
        _workspace = os.environ.get(
            "OPENCLAW_WORKSPACE",
            str(Path.home() / ".openclaw" / "workspace"),
        )
        _scripts_dir = os.path.join(_workspace, "scripts")
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        import yaoyao_bridge as yb
        _yaoyao_bridge = yb
    except Exception as e:
        logger.warning(f"yaoyao_bridge 导入失败(非致命): {e}")
        _yaoyao_bridge = False  # 标记失败,避免重试
    return _yaoyao_bridge if _yaoyao_bridge else None


# ── 路由统计辅助函数 ──
def _try_record_routing_fallback(stats_path):
    """记录一次 depth 1 直答回退（轻量检索失败时调用）"""
    import json
    try:
        _rs = {"depth1_total": 0, "depth1_fallbacks": 0}
        if stats_path and os.path.exists(stats_path):
            with open(stats_path, 'r') as _f:
                _rs = json.load(_f)
        _rs['depth1_total'] = _rs.get('depth1_total', 0) + 1
        _rs['depth1_fallbacks'] = _rs.get('depth1_fallbacks', 0) + 1
        os.makedirs(os.path.dirname(stats_path), exist_ok=True)
        with open(stats_path, 'w') as _f:
            json.dump(_rs, _f)
    except Exception:
        pass


class XiaoYiClawLLM:
    """
    小艺 Claw 大模型统一接口

    整合能力:
    - 向量检索 (FAISS/Qdrant/sqlite-vec)
    - 知识图谱 (ontology)
    - 个人知识库 (2nd-brain)
    - 四层记忆 (memory-tencentdb)
    - 多模态理解 (xiaoyi-image)
    - 底层引擎 (XiaoyiMemoryV2 - 13层/44工作流/129模块)
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}

        # KV Cache 会话 ID - 用于小艺通道的 X-Conversation-Id 复用
        self._kv_session_id = config.get("session_id") if config else None
        if not self._kv_session_id:
            # 固定 ID,复用 KV Cache
            self._kv_session_id = "xiaoyi-claw-main"

        # 全透明事件发布：Worker 层 ZMQ PUB 回调
        # 自定义插件可通过 api.on('rccam:*', cb) 订阅
        self._pub_event = (config or {}).get("pub_event_fn")

        # 初始化各模块
        self._init_vector_store()
        self._init_ontology_bridge()
        self._init_brain_sync()
        self._init_multimodal()
        self._init_scorer()
        self._init_forgetter()
        self._init_learner()
        self._init_dag()
        self._init_task_bridge()
        self._init_ocr2()
        self._init_memory_v2()
        self._init_llm_client()
        self._init_synapse_network()
        self._init_emotion_memory()
        self._init_adaptive_memory()
        self._init_skill_coordinator()

        # Galaxy KoRa: 行为模式数据
        self._kora_behavior_requests: List[Dict] = []
        self._kora_behavior_max = 200
        self._kora_patterns: List[Dict] = []
        self._kora_last_refresh = 0.0
        self._kora_refresh_interval = 3600  # 每小时刷新一次

        logger.info("小艺 Claw 大模型初始化完成")

    def _init_vector_store(self):
        """初始化向量存储(优先 hnswlib,降级 sqlite)"""
        try:
            from unified_vector_store import UnifiedVectorStore
            backend = self.config.get('vector_backend', 'hnswlib')
            self.vector_store = UnifiedVectorStore(
                backend=backend,
                dim=self.config.get('vector_dim', 1024)
            )
        except Exception as e:
            logger.warning(f"向量存储初始化失败: {e}")
            self.vector_store = None

    def _init_ontology_bridge(self):
        """初始化知识图谱桥接"""
        try:
            from memory_ontology_bridge import MemoryOntologyBridge
            self.ontology_bridge = MemoryOntologyBridge()
        except Exception as e:
            logger.warning(f"知识图谱桥接初始化失败: {e}")
            self.ontology_bridge = None

    def _init_brain_sync(self):
        """初始化知识库同步"""
        try:
            from brain_memory_sync import BrainMemorySync
            self.brain_sync = BrainMemorySync()
        except Exception as e:
            logger.warning(f"知识库同步初始化失败: {e}")
            self.brain_sync = None

    def _init_multimodal(self):
        """初始化多模态存储"""
        try:
            from multimodal_memory import MultimodalMemoryStore
            self.multimodal_store = MultimodalMemoryStore()
        except Exception as e:
            logger.warning(f"多模态存储初始化失败: {e}")
            self.multimodal_store = None

    def _init_scorer(self):
        """初始化重要性评分器"""
        try:
            from importance_scorer import ImportanceScorer
            self.scorer = ImportanceScorer()
        except Exception as e:
            logger.warning(f"重要性评分器初始化失败: {e}")
            self.scorer = None

    def _init_forgetter(self):
        """初始化智能遗忘"""
        try:
            from smart_forgetter import SmartForgetter
            self.forgetter = SmartForgetter()
        except Exception as e:
            logger.warning(f"智能遗忘初始化失败: {e}")
            self.forgetter = None

    def _init_learner(self):
        """初始化自主学习"""
        try:
            from auto_learner import AutoLearner
            self.learner = AutoLearner()
        except Exception as e:
            logger.warning(f"自主学习初始化失败: {e}")
            self.learner = None

    def _init_task_bridge(self):
        """初始化任务桥接"""
        try:
            from task_memory_bridge import TaskMemoryBridge
            self.task_bridge = TaskMemoryBridge()
        except Exception as e:
            logger.warning(f"任务桥接初始化失败: {e}")
            self.task_bridge = None

    def _init_ocr2(self):
        """初始化 DeepSeek-OCR-2 适配器"""
        try:
            from deepseek_ocr2_adapter import get_adapter
            self.ocr2 = get_adapter()
            logger.info("DeepSeek-OCR-2 初始化成功")
        except Exception as e:
            logger.warning(f"DeepSeek-OCR-2 初始化失败: {e}")
            self.ocr2 = None

    def _init_memory_v2(self):
        """初始化 XiaoyiMemoryV2 底层引擎(作为子模块挂入)"""
        try:
            from xiaoyi_memory import XiaoyiMemoryV2
            # workspace_path: xiaoyi_claw_api.py 在 core/ 下,上层两层是 workspace
            ws = str(Path(__file__).parent.parent)
            self.memory_v2 = XiaoyiMemoryV2(workspace_path=ws)
            logger.info("XiaoyiMemoryV2 初始化成功")
        except Exception as e:
            logger.warning(f"XiaoyiMemoryV2 初始化失败: {e}")
            self.memory_v2 = None

    def _init_dag(self):
        """初始化 DAG 上下文管理器"""
        self.dag = None
        try:
            from dag_context_manager import DAGContextManager
            dag_db = str(Path.home() / ".openclaw/workspace/.dag_context.db")
            self.dag = DAGContextManager(
                db_path=dag_db,
                max_context_tokens=240000,
                fresh_tail_count=10,
                leaf_chunk_tokens=8000,
            )
            logger.info("DAG 上下文管理器初始化成功")
        except Exception as e:
            logger.warning(f"DAG 上下文管理器初始化失败: {e}")

    def _init_synapse_network(self):
        """初始化突触网络（增强模块）"""
        try:
            from memory_synapse_network import MemorySynapseNetwork
            ws = str(Path(__file__).parent.parent)
            self.synapse_network = MemorySynapseNetwork(workspace_path=ws)
            logger.info("MemorySynapseNetwork 初始化成功")
        except Exception as e:
            logger.warning(f"MemorySynapseNetwork 初始化失败: {e}")
            self.synapse_network = None

    def _init_emotion_memory(self):
        """初始化情感记忆（增强模块）"""
        try:
            from emotion_memory import EmotionMemoryManager
            ws = str(Path(__file__).parent.parent)
            self.emotion_memory = EmotionMemoryManager(workspace_path=ws)
            logger.info("EmotionMemoryManager 初始化成功")
        except Exception as e:
            logger.warning(f"EmotionMemoryManager 初始化失败: {e}")
            self.emotion_memory = None

    def _init_adaptive_memory(self):
        """初始化自适应记忆（增强模块）"""
        try:
            from adaptive_memory import AdaptiveMemoryManager
            ws = str(Path(__file__).parent.parent)
            self.adaptive_memory = AdaptiveMemoryManager(workspace_path=ws)
            logger.info("AdaptiveMemoryManager 初始化成功")
        except Exception as e:
            logger.warning(f"AdaptiveMemoryManager 初始化失败: {e}")
            self.adaptive_memory = None

    def _init_skill_coordinator(self):
        """初始化技能协调器（增强模块）"""
        try:
            from skill_coordinator import SkillCoordinator
            self.skill_coordinator = SkillCoordinator()
            logger.info("SkillCoordinator 初始化成功")
        except Exception as e:
            logger.warning(f"SkillCoordinator 初始化失败: {e}")
            self.skill_coordinator = None

    def _init_llm_client(self):
        """初始化 LLM 客户端(DeepSeek V4 Flash + Pro)

        双源策略:
        1. llm_config.json - 自有配置,不受 OpenClaw 版本更新影响
        2. openclaw.json - 同步 OpenClaw 提供商的更新(如 base_url/模型升级)

        两个源都可用时,model/url/key 从 openclaw.json 读取(最新),
        但 llm_config.json 始终作为兜底。
        """
        self.llm_flash = None
        self.llm_pro = None
        try:
            # ===== 第一源:自有 llm_config.json(不受 OpenClaw 影响) =====
            # 优先搜 skill 内部 config 目录,兜底搜 skills/llm-memory-integration/config
            _script_dir = Path(__file__).parent.parent
            config_path = _script_dir / "config" / "llm_config.json"
            if not config_path.exists():
                _alt = _script_dir / "skills" / "llm-memory-integration" / "config" / "llm_config.json"
                if _alt.exists():
                    config_path = _alt
            fallback_flash = None
            fallback_pro = None

            if config_path.exists():
                import json
                with open(config_path, "r", encoding="utf-8") as f:
                    llm_cfg = json.load(f)
                flash_cfg = llm_cfg.get("llm", {})
                pro_cfg = llm_cfg.get("llm_pro", {})
                if flash_cfg.get("api_key"):
                    fallback_flash = {
                        "baseUrl": flash_cfg["base_url"],
                        "apiKey": flash_cfg["api_key"],
                        "model": flash_cfg.get("model", "deepseek-v4-flash"),
                    }
                if pro_cfg.get("api_key"):
                    fallback_pro = {
                        "baseUrl": pro_cfg["base_url"],
                        "apiKey": pro_cfg["api_key"],
                        "model": pro_cfg.get("model", "deepseek-v4-pro"),
                    }

            # ===== 第二源:openclaw.json 同步(可选,OpenClaw 升级后可能重置) =====
            flash_provider = fallback_flash
            pro_provider = fallback_pro

            openclaw_cfg_path = Path(os.environ.get("OPENCLAW_CONFIG",
                os.path.expanduser("~/.openclaw/openclaw.json")))

            if openclaw_cfg_path.exists():
                import json
                with open(openclaw_cfg_path, "r", encoding="utf-8") as f:
                    oc_cfg = json.load(f)
                providers = oc_cfg.get("models", {}).get("providers", {})

                # 如果 deepseek provider 还在,用它覆盖(可能包含更新的 base_url 或模型名)
                ds = providers.get("deepseek", {})
                if ds.get("baseUrl") and ds.get("apiKey"):
                    ds_models = {m["id"]: m for m in ds.get("models", [])}
                    for mid in ds_models:
                        if "flash" in mid.lower():
                            flash_provider = {
                                "baseUrl": ds["baseUrl"],
                                "apiKey": ds["apiKey"],
                                "model": mid,
                            }
                        elif "pro" in mid.lower():
                            pro_provider = {
                                "baseUrl": ds["baseUrl"],
                                "apiKey": ds["apiKey"],
                                "model": mid,
                            }
                    logger.info(f"从 openclaw.json 同步模型: flash={flash_provider['model'] if flash_provider else None}, pro={pro_provider['model'] if pro_provider else None}")

            # ===== 初始化客户端 =====
            if flash_provider:
                from openai import OpenAI
                self.llm_flash = OpenAI(
                    api_key=flash_provider["apiKey"],
                    base_url=flash_provider["baseUrl"],
                )
                self._llm_flash_model = flash_provider["model"]
            else:
                logger.warning("Flash 客户端未初始化(两个配置源均不可用)")

            if pro_provider:
                from openai import OpenAI
                self.llm_pro = OpenAI(
                    api_key=pro_provider["apiKey"],
                    base_url=pro_provider["baseUrl"],
                )
                self._llm_pro_model = pro_provider["model"]
            else:
                logger.warning("Pro 客户端未初始化(两个配置源均不可用)")

            logger.info(f"LLM 客户端初始化: flash={self.llm_flash is not None}, pro={self.llm_pro is not None}")

            # 增强思考引擎(Reflexion + Self-Refine + MultiPath + Flash NLP)
            self.thinking_enhanced = None
            try:
                from thinking_enhanced import ThinkingEnhanced
                self.thinking_enhanced = ThinkingEnhanced(self.llm_flash)
                logger.info("增强思考引擎已加载")
            except Exception as e:
                logger.warning(f"增强思考引擎加载失败: {e}")

        except Exception as e:
            logger.warning(f"LLM 客户端初始化失败: {e}")

    # ==================== 记忆管理 API ====================

    def classify_knowledge(self, content: str) -> Dict:
        """
        知识分类 - 基于关键词自动分类

        Args:
            content: 待分类内容

        Returns:
            分类结果,如 {"type": "decision", "confidence": 0.8}
        """
        keywords = {
            "decision": ["决定", "采用", "选择", "配置变更", "方案"],
            "error": ["错误", "失败", "bug", "修复", "问题"],
            "learning": ["学会", "理解", "发现", "掌握", "注意"],
            "task": ["需要做", "计划", "下一步", "TODO", "待办"],
            "preference": ["喜欢", "偏好", "倾向", "习惯", "想要"],
            "progress": ["完成", "达成", "里程碑", "搞定"],
        }

        best_type = "info"
        best_confidence = 0.5

        for mem_type, words in keywords.items():
            matches = sum(1 for w in words if w in content)
            if matches > 0:
                confidence = min(0.5 + matches * 0.1, 0.95)
                if confidence > best_confidence:
                    best_type = mem_type
                    best_confidence = confidence

        return {"type": best_type, "confidence": best_confidence}

    def remember(self,
                 content: str,
                 metadata: Optional[Dict] = None,
                 vector: Optional[List[float]] = None,
                 source: str = 'user') -> str:
        """
        存储记忆(双写:向量存储 + XiaoyiMemoryV2)

        Args:
            content: 记忆内容
            metadata: 元数据
            vector: 向量(可选,不提供则仅存储文本)
            source: 来源标识

        Returns:
            记忆 ID
        """
        memory_id = str(uuid.uuid4())
        metadata = metadata or {}
        metadata['memory_id'] = memory_id
        metadata['source'] = source
        metadata['created_at'] = datetime.now().isoformat()

        # 存储到向量库
        if self.vector_store and vector:
            self.vector_store.add_vectors(
                vectors=[vector],
                contents=[content],
                metadatas=[metadata],
                ids=[memory_id],
                source=source
            )

        # 双写到 XiaoyiMemoryV2
        if self.memory_v2:
            try:
                self.memory_v2.store(content, source=source)
            except Exception as e:
                logger.warning(f"XiaoyiMemoryV2 store 失败: {e}")

        # 写入 DAG 上下文管理器
        if self.dag:
            try:
                self.dag.add_message(
                    session_key="xiaoyi-claw-dag",
                    role=source,
                    content=content,
                    tokens=len(content) // 2,
                    importance=0.5,
                )
            except Exception as e:
                logger.warning(f"DAG 写入失败: {e}")

        # 关联到知识图谱
        if self.ontology_bridge:
            self.ontology_bridge.link_memory_to_entities(memory_id, content)

        # 同步到知识库(如果是重要实体)
        if self.brain_sync and metadata.get('is_entity'):
            self.brain_sync.sync_memory_to_brain(memory_id, content, metadata)

        # 三写到 Yaoyao .yaoyao.db(FTS5 + vec,失败不中断)
        yaoyao = _get_yaoyao_bridge()
        if yaoyao:
            try:
                yaoyao.store_to_yaoyao(content, source=source)
            except Exception as e:
                logger.warning(f"Yaoyao store 失败(非致命): {e}")

        logger.info(f"存储记忆: {memory_id}")
        return memory_id

    def _load_dag_summaries(self, top_k: int = 5) -> List[Dict]:
        """
        从 DAG 加载节点,供 recall 使用

        优先摘要节点,无摘要时回退拉最近消息节点

        Args:
            top_k: 最大返回数

        Returns:
            格式化为标准结果列表的节点
        """
        if not self.dag:
            return []

        try:
            # 先试摘要节点
            summary_nodes = self.dag.get_session_nodes(
                session_key="xiaoyi-claw-dag",
                node_type="summary",
                limit=top_k
            )
            if summary_nodes:
                results = []
                for node in summary_nodes:
                    results.append({
                        "id": node.node_id,
                        "content": node.content,
                        "source": "dag_summary",
                        "score": 0.7 + node.importance_score * 0.1,
                        "metadata": {
                            "type": "dag_summary",
                            "timestamp": node.timestamp,
                            "keywords": node.keywords,
                        }
                    })
                return results

            # 无摘要时回退拉最近消息节点
            message_nodes = self.dag.get_session_nodes(
                session_key="xiaoyi-claw-dag",
                node_type="message",
                limit=top_k
            )
            if message_nodes:
                results = []
                for node in reversed(message_nodes):
                    results.append({
                        "id": node.node_id,
                        "content": node.content,
                        "source": "dag_message",
                        "score": 0.5,
                        "metadata": {
                            "type": "dag_message",
                            "timestamp": node.timestamp,
                        }
                    })
                return results

            return []
        except Exception as e:
            logger.warning(f"加载 DAG 节点失败: {e}")
            return []

    def recall(self,
               query: str,
               query_vector: Optional[List[float]] = None,
               top_k: int = 10,
               source_filter: Optional[str] = None,
               enhance_with_kg: bool = True) -> List[Dict]:
        """
        检索记忆(双路融合:向量存储 + XiaoyiMemoryV2 增强检索)

        Args:
            query: 查询文本
            query_vector: 查询向量
            top_k: 返回数量
            source_filter: 来源过滤
            enhance_with_kg: 是否用知识图谱增强

        Returns:
            记忆列表
        """
        results = []

        # 向量检索(主路)
        if self.vector_store and query_vector:
            results = self.vector_store.search(
                query_vector=query_vector,
                top_k=top_k,
                source_filter=source_filter
            )

        # XiaoyiMemoryV2 增强检索(辅路)
        v2_results = []
        if self.memory_v2:
            try:
                v2_out = self.memory_v2.enhanced_recall(query, top_k=top_k)
                if isinstance(v2_out, dict):
                    v2_results = v2_out.get("basic_results", v2_out.get("results", []))
                elif isinstance(v2_out, list):
                    v2_results = v2_out
            except Exception as e:
                logger.warning(f"XiaoyiMemoryV2 recall 失败: {e}")

        # Yaoyao .yaoyao.db 混合搜索(第三路:FTS5 + Vector + RRF)
        yaoyao_results = []
        yaoyao = _get_yaoyao_bridge()
        if yaoyao:
            try:
                yaoyao_results = yaoyao.search_yaoyao(query, limit=top_k)
            except Exception as e:
                logger.warning(f"Yaoyao search 失败(非致命): {e}")

        # 三路 RRF 融合
        all_results = [r for r in [results, v2_results, yaoyao_results] if r]
        if len(all_results) == 2:
            fused = self._rrf_fuse(all_results[0], all_results[1], k=60)
            results = fused[:top_k]
        elif len(all_results) == 3:
            # 三路融合:先融合前两路,再融合第三路
            fused1 = self._rrf_fuse(all_results[0], all_results[1], k=60)
            fused2 = self._rrf_fuse(fused1, all_results[2], k=60)
            results = fused2[:top_k]
        elif len(all_results) == 1:
            results = all_results[0][:top_k]

        # Yaoyao 结果加权调整(vector 路 0.6, FTS5 路 0.4)
        if yaoyao_results:
            for r in results:
                if r.get("source") == "yaoyao_vec":
                    r["score"] *= 0.6
                elif r.get("source") == "yaoyao_fts":
                    r["score"] *= 0.4
            results.sort(key=lambda x: x.get("score", 0), reverse=True)

        # 知识图谱增强
        if enhance_with_kg and self.ontology_bridge and results:
            results = self.ontology_bridge.enhance_search_results(results, query)

        # DAG 上下文补充(加载已有摘要)
        dag_summaries = self._load_dag_summaries(top_k)
        if dag_summaries:
            # 已有摘要放前面,优先带上下文
            results = dag_summaries + results[:top_k - len(dag_summaries)]

        # 知识库补充
        if self.brain_sync and len(results) < top_k:
            brain_results = self.brain_sync.brain.search_entries(query)
            for entry in brain_results[:top_k - len(results)]:
                results.append({
                    'id': entry.id,
                    'content': entry.content,
                    'metadata': {
                        'source': 'brain',
                        'category': entry.category,
                        'name': entry.name
                    },
                    'score': 0.5
                })

        return results

    def _rrf_fuse(self, list_a: List[Dict], list_b: List[Dict], k: int = 60) -> List[Dict]:
        """RRF 融合两个检索结果列表"""
        from collections import OrderedDict
        scores = {}
        for rank, item in enumerate(list_a):
            item_id = item.get("id", item.get("content", str(rank)))
            scores[item_id] = scores.get(item_id, 0) + 1.0 / (k + rank + 1)
        for rank, item in enumerate(list_b):
            item_id = item.get("id", item.get("content", str(rank)))
            scores[item_id] = scores.get(item_id, 0) + 1.0 / (k + rank + 1)
        seen = set()
        fused = []
        for item in list_a + list_b:
            item_id = item.get("id", item.get("content", ""))
            if item_id not in seen:
                seen.add(item_id)
                item["score"] = scores.get(item_id, 0)
                fused.append(item)
        fused.sort(key=lambda x: x.get("score", 0), reverse=True)
        return fused

    def forget(self,
               memory_id: Optional[str] = None,
               criteria: Optional[Dict] = None) -> int:
        """
        删除记忆(支持单条或批量条件删除)

        Args:
            memory_id: 记忆 ID
            criteria: 删除条件
                - source: 按来源过滤
                - before_date: 按时间过滤(ISO 格式字符串)
                - min_importance: 最低重要性阈值,低于此值的删除
                - all: 设为 True 则清空所有

        Returns:
            删除数量
        """
        count = 0

        # 单条删除
        if memory_id:
            if self.vector_store:
                return self.vector_store.delete([memory_id])
            return 0

        # 批量删除
        if criteria:
            # 按条件检索待删除项
            candidates = []

            if 'source' in criteria or 'before_date' in criteria:
                # 遍历向量库查找匹配项(通过搜索全量)
                if self.vector_store:
                    try:
                        all_memories = self.vector_store.list_all() if hasattr(self.vector_store, 'list_all') else []
                        for mem in all_memories:
                            meta = mem.get('metadata', {})
                            match = True
                            if 'source' in criteria and meta.get('source') != criteria['source']:
                                match = False
                            if 'before_date' in criteria:
                                created = meta.get('created_at', '')
                                if created and created >= criteria['before_date']:
                                    match = False
                            if match:
                                candidates.append(mem.get('id', mem.get('memory_id', '')))
                    except Exception:
                        pass

            if 'min_importance' in criteria:
                min_imp = criteria['min_importance']
                if self.scorer:
                    try:
                        low_imp = self.scorer.get_low_importance_memories(threshold=min_imp)
                        for mem in low_imp:
                            mid = mem.get('id', mem.get('memory_id', ''))
                            if mid:
                                candidates.append(mid)
                    except Exception:
                        pass

            if criteria.get('all'):
                if self.vector_store and hasattr(self.vector_store, 'clear_all'):
                    count = self.vector_store.clear_all()
                    logger.warning(f"清空所有记忆: {count}")
                    return count

            # 去重并删除
            if candidates:
                unique_ids = list(set(candidates))
                if self.vector_store:
                    count = self.vector_store.delete(unique_ids)

            # 同时清理 XiaoyiMemoryV2
            if self.memory_v2 and count > 0:
                try:
                    self.memory_v2.delete_memories(count)
                except Exception:
                    pass

        return count

    # ==================== 知识图谱 API ====================

    def link(self,
             entity1_id: str,
             entity2_id: str,
             relation: str,
             properties: Optional[Dict] = None) -> bool:
        """
        创建实体关联(通过记忆桥接实现)

        ontology_bridge 目前支持 link_memory_to_entities 接口,
        entity-entity 直接关联暂不支持,通过记忆内容桥接。

        Args:
            entity1_id: 实体1 ID
            entity2_id: 实体2 ID
            relation: 关系类型
            properties: 关系属性

        Returns:
            是否成功
        """
        if not self.ontology_bridge:
            return False

        try:
            # 通过 link_memory_to_entities 建立关联
            content = f"{entity1_id} {relation} {entity2_id}"
            if properties:
                content += f" ({json.dumps(properties, ensure_ascii=False)})"
            self.ontology_bridge.link_memory_to_entities(f"relation:{entity1_id}:{entity2_id}", content)
            logger.info(f"创建实体关联: {entity1_id} -[{relation}]-> {entity2_id}")
            return True
        except Exception as e:
            logger.warning(f"创建实体关联失败: {e}")
            return False

    def get_entity(self, entity_name: str) -> Dict[str, Any]:
        """
        获取实体信息

        Args:
            entity_name: 实体名称

        Returns:
            实体信息
        """
        if self.ontology_bridge:
            return self.ontology_bridge.get_entity_context(entity_name)
        return {'found': False, 'name': entity_name}

    # ==================== 学习 API ====================

    def learn(self,
              feedback: Dict[str, Any]) -> bool:
        """
        学习反馈(支持正向/负向/修正反馈)

        Args:
            feedback: 反馈信息
                - memory_id: 记忆 ID
                - action: 'positive' / 'negative' / 'correct'
                - correction: 修正内容(可选)

        Returns:
            是否成功
        """
        memory_id = feedback.get('memory_id')
        action = feedback.get('action')

        if action == 'positive':
            # 正面反馈,提升重要性
            if memory_id and self.scorer:
                try:
                    self.scorer.record_feedback(memory_id, 1.0)
                    logger.info(f"正面反馈: {memory_id},记录反馈")
                except Exception as e:
                    logger.warning(f"记录反馈失败: {e}")

        elif action == 'negative':
            # 负面反馈,降低重要性
            if memory_id and self.scorer:
                try:
                    self.scorer.record_feedback(memory_id, -1.0)
                    logger.info(f"负面反馈: {memory_id},记录反馈")
                except Exception as e:
                    logger.warning(f"记录反馈失败: {e}")
            # 同时标记为低质量
            if self.vector_store and memory_id:
                try:
                    self.vector_store.update_metadata(memory_id, {"quality": "low", "last_feedback": "negative"})
                except Exception:
                    pass

        elif action == 'correct':
            # 修正内容
            correction = feedback.get('correction')
            if correction and memory_id:
                logger.info(f"修正记忆: {memory_id} -> {correction[:50]}...")
                # 存储修正后的内容为新记忆,关联原记忆
                new_id = self.remember(
                    content=correction,
                    metadata={
                        "corrected_from": memory_id,
                        "original_content": feedback.get('original', ''),
                        "source": "user_correction"
                    },
                    source="user"
                )
                # 标记原记忆为已修正
                if self.vector_store and memory_id:
                    try:
                        self.vector_store.update_metadata(memory_id, {
                            "corrected": True,
                            "correction_id": new_id,
                            "quality": "corrected"
                        })
                    except Exception:
                        pass
                # 也同步到 learner
                if self.learner:
                    try:
                        original = feedback.get('original', '')
                        if original:
                            self.learner.learn_correction(original, correction)
                    except Exception:
                        pass

        return True

    # ==================== 多模态 API ====================

    def remember_image(self,
                       image_data: bytes,
                       description: str,
                       tags: Optional[List[str]] = None,
                       entities: Optional[List[str]] = None) -> str:
        """
        存储图像记忆

        Args:
            image_data: 图像二进制数据
            description: 图像描述
            tags: 标签列表
            entities: 关联实体列表

        Returns:
            图像记忆 ID
        """
        if self.multimodal_store:
            return self.multimodal_store.store_image(image_data, description, tags, entities)
        return ''

    def recall_images(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        检索图像记忆

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            图像记忆列表
        """
        if self.multimodal_store:
            return self.multimodal_store.search(query, top_k)
        return []

    # ==================== OCR2 图像理解 API ====================

    def understand_image(self,
                         image_source,
                         mode: str = 'general',
                         prompt: Optional[str] = None) -> Dict[str, Any]:
        """
        图像理解(DeepSeek-OCR-2)

        Args:
            image_source: 图片源(URL、文件路径、二进制数据)
            mode: 理解模式
                - general: 通用理解
                - ocr: 文字识别
                - document: 文档解析
                - chart: 图表分析
                - table: 表格识别
                - handwriting: 手写识别
                - complex: 复杂版式
            prompt: 自定义提示词(可选)

        Returns:
            {
                'success': bool,
                'content': str,
                'mode': str,
                'tokens_used': int,
                'latency_ms': float
            }
        """
        if not self.ocr2:
            return {
                'success': False,
                'content': 'OCR2 未初始化',
                'mode': mode,
                'tokens_used': 0,
                'latency_ms': 0
            }

        from deepseek_ocr2_adapter import ImageUnderstandingMode

        # 映射模式字符串到枚举
        mode_map = {
            'general': ImageUnderstandingMode.GENERAL,
            'ocr': ImageUnderstandingMode.OCR,
            'document': ImageUnderstandingMode.DOCUMENT,
            'chart': ImageUnderstandingMode.CHART,
            'table': ImageUnderstandingMode.TABLE,
            'handwriting': ImageUnderstandingMode.HANDWRITING,
            'complex': ImageUnderstandingMode.COMPLEX_LAYOUT
        }

        ocr_mode = mode_map.get(mode, ImageUnderstandingMode.GENERAL)

        result = self.ocr2.understand(image_source, prompt=prompt, mode=ocr_mode)

        return {
            'success': result.success,
            'content': result.content,
            'mode': mode,
            'tokens_used': result.tokens_used,
            'latency_ms': result.latency_ms,
            'confidence': result.confidence
        }

    def ocr_image(self, image_source) -> Dict[str, Any]:
        """
        OCR 文字识别(快捷方法)

        Args:
            image_source: 图片源

        Returns:
            识别结果
        """
        return self.understand_image(image_source, mode='ocr')

    def parse_document(self, image_source) -> Dict[str, Any]:
        """
        文档解析(快捷方法)

        Args:
            image_source: 图片源

        Returns:
            解析结果
        """
        return self.understand_image(image_source, mode='document')

    def analyze_chart(self, image_source) -> Dict[str, Any]:
        """
        图表分析(快捷方法)

        Args:
            image_source: 图片源

        Returns:
            分析结果
        """
        return self.understand_image(image_source, mode='chart')

    def verify_image_claim(self,
                           image_source,
                           claim: str) -> Dict[str, Any]:
        """
        验证图像声明(防幻觉)

        Args:
            image_source: 图片源
            claim: 待验证的声明

        Returns:
            {
                'verified': bool,
                'confidence': float,
                'evidence': str
            }
        """
        if not self.ocr2:
            return {
                'verified': False,
                'confidence': 0,
                'evidence': 'OCR2 未初始化'
            }

        result = self.ocr2.verify_claim(image_source, claim)

        return {
            'verified': result.get('verified', False),
            'confidence': result.get('confidence', 0),
            'evidence': result.get('evidence', '')
        }

    # ==================== 智能遗忘 API ====================

    def analyze_forget(self, memories: List[Dict]) -> Dict[str, List]:
        """
        分析遗忘候选

        Args:
            memories: 记忆列表

        Returns:
            分类结果
        """
        if self.forgetter:
            return self.forgetter.analyze(memories)
        return {'keep': [], 'archive': [], 'delete': []}

    def run_cleanup(self, memories: List[Dict], dry_run: bool = True) -> Dict[str, Any]:
        """
        运行清理

        Args:
            memories: 记忆列表
            dry_run: 试运行模式

        Returns:
            清理结果
        """
        if self.forgetter:
            return self.forgetter.run_cleanup(memories, dry_run=dry_run)
        return {'dry_run': True, 'analysis': {}, 'executed': None}

    # ==================== 学习 API ====================

    def learn_preference(self, key: str, value: Any):
        """学习偏好"""
        if self.learner:
            self.learner.learn_preference(key, value)

    def learn_correction(self, original: str, corrected: str):
        """学习纠正"""
        if self.learner:
            self.learner.learn_correction(original, corrected)

    def get_preference(self, key: str, default: Any = None) -> Any:
        """获取偏好"""
        if self.learner:
            return self.learner.get_preference(key, default)
        return default

    def auto_learn(self, user_input: str, assistant_response: str, feedback: Optional[str] = None):
        """自动学习"""
        if self.learner:
            self.learner.auto_learn_from_interaction(user_input, assistant_response, feedback)

    # ==================== 任务关联 API ====================

    def link_task(self, task_id: str, memory_id: str, link_type: str = 'related_to'):
        """关联任务和记忆"""
        if self.task_bridge:
            self.task_bridge.link_task_to_memory(task_id, memory_id, link_type)

    def get_task_memories(self, task_id: str) -> List[str]:
        """获取任务关联的记忆"""
        if self.task_bridge:
            return self.task_bridge.get_memories_for_task(task_id)
        return []

    # ==================== 系统状态 API ====================

    def answer(self,
               query: str,
               top_k: int = 5,
               min_confidence: float = 0.3) -> Dict[str, Any]:
        """
        生成回答(带完整验证流程)

        从记忆系统中召回相关记忆,通过 HallucinationGuard 验证后输出。

        Args:
            query: 用户查询
            top_k: 召回记忆数量
            min_confidence: 最小置信度

        Returns:
            {
                "answer": str,  # 最终回答
                "confidence": float,  # 整体置信度
                "sources": List[str],  # 来源摘要
                "validation": Dict  # 验证信息
            }
        """
        memories = self.recall(query, top_k=top_k)

        if not memories:
            return {
                "answer": "我没有找到相关的记忆信息。",
                "confidence": 0.0,
                "sources": [],
                "validation": {"no_results": True}
            }

        # 通过 HallucinationGuard 验证内容
        verified_memories = []
        hallucination_warnings = []

        if self.memory_v2 and hasattr(self.memory_v2, 'hallucination_guard'):
            guard = self.memory_v2.hallucination_guard
            for mem in memories:
                content = mem.get("content", "")
                mem_id = mem.get("id", "")
                try:
                    result = guard.verify(content, source=mem.get("source", "memory"))
                    if result.get("status", "verified") != "fabricated":
                        verified_memories.append(mem)
                    else:
                        hallucination_warnings.append({"id": mem_id, "reason": result.get("reason", "")})
                except Exception:
                    verified_memories.append(mem)
        else:
            # 降级:直接使用基础结果
            verified_memories = memories

        if not verified_memories:
            return {
                "answer": "我找到了一些相关信息,但经过验证后发现准确性不足,建议你补充更多细节。",
                "confidence": 0.0,
                "sources": [],
                "validation": {"all_filtered": True, "filtered_count": len(hallucination_warnings)}
            }

        # 计算置信度
        raw_confidence = sum(m.get("score", m.get("confidence", 0.5)) for m in verified_memories) / len(verified_memories)

        # 如果有防幻觉系统介入,保守调整置信度
        if hallucination_warnings:
            confidence = raw_confidence * 0.85  # 有过滤过的结果,保守处理
        else:
            confidence = raw_confidence

        # 不确定性表达
        if confidence < 0.3:
            answer = f"我不太确定,不过根据记忆系统找到的信息:{verified_memories[0].get('content', '')}"
        elif confidence < 0.6:
            answer = f"从已有记忆来看:{verified_memories[0].get('content', '')}"
        else:
            answer = verified_memories[0].get("content", "")

        top_contents = [m.get("content", "")[:50] for m in verified_memories[:3]]

        return {
            "answer": answer,
            "confidence": confidence,
            "sources": [s for s in top_contents if s],
            "validation": {
                "verified": True,
                "total": len(memories),
                "verified_count": len(verified_memories),
                "filtered_count": len(hallucination_warnings),
                "hallucination_warnings": hallucination_warnings[:3]
            }
        }

    def correct(self,
                original: str,
                corrected: str) -> Dict[str, Any]:
        """
        处理用户纠正

        将纠正内容存储为记忆,并标记原始信息为需重新验证。

        Args:
            original: 原始内容
            corrected: 纠正后内容

        Returns:
            {"correction_id": str, "message": str}
        """
        correction_id = self.remember(
            content=corrected,
            metadata={
                "is_correction": True,
                "original": original,
                "source": "user_correction"
            },
            source="user"
        )

        logger.info(f"用户纠正: {original} -> {corrected}")

        return {
            "correction_id": correction_id,
            "message": "已记录纠正,原始信息已标记为需重新验证"
        }

    # ==================== 优化模块集成(从 optimization_integration 融合)====================

    def _get_optimization_integration(self):
        """懒加载 OptimizationIntegration"""
        if not hasattr(self, '_opt_integration') or self._opt_integration is None:
            from optimization_integration import OptimizationIntegration
            self._opt_integration = OptimizationIntegration()
        return self._opt_integration

    def optimize_query_processing(self, query: str, context: Optional[str] = None) -> Dict[str, Any]:
        """优化查询处理"""
        return self._get_optimization_integration().optimize_query_processing(query, context)

    def optimize_retrieval(self, scores: List[float], query: str = "", query_type: str = "general") -> Dict[str, Any]:
        """优化检索"""
        return self._get_optimization_integration().optimize_retrieval(scores, query, query_type)

    def optimize_memory_synapse(self, synapse_state, operation: str = "ltp") -> Dict[str, Any]:
        """优化记忆突触"""
        from optimization_integration import SynapseState
        if not isinstance(synapse_state, SynapseState):
            synapse_state = SynapseState(weight=float(synapse_state))
        return self._get_optimization_integration().optimize_memory_synapse(synapse_state, operation)

    # ==================== 腾讯云记忆集成(从 tencentdb_integration 融合)====================

    def _get_tencentdb(self):
        """懒加载 TencentDBMemory"""
        if not hasattr(self, '_tencentdb') or self._tencentdb is None:
            from tencentdb_integration import TencentDBMemory
            self._tencentdb = TencentDBMemory()
        return self._tencentdb

    def search_tencentdb(self, query: str, limit: int = 10) -> List[Dict]:
        """搜索腾讯云记忆"""
        return self._get_tencentdb().search_memories(query, limit)

    def get_tencentdb_stats(self) -> Dict:
        """获取腾讯云记忆统计"""
        return self._get_tencentdb().get_stats()

    def get_persona(self) -> Optional[str]:
        """获取用户画像"""
        return self._get_tencentdb().get_persona()

    def get_scene_blocks(self) -> List[Dict]:
        """获取场景块"""
        return self._get_tencentdb().get_scene_blocks()

    def sync_tencentdb(self) -> Dict:
        """同步腾讯云记忆"""
        return self._get_tencentdb().sync_to_xiaoyi_memory()

    def _recall_memory_unified(self, query: str, max_results: int = 10) -> List[Dict]:
        """
        统一记忆召回(读取本地 memory/ 目录下的每日记忆文件)

        替代 memory_unified.py.merged 的 UnifiedMemory.recall():
        - 不依赖 yaoyao-memory-v2 的子进程调用
        - 直接从文件系统读取每日记忆文件做关键词匹配
        - 与 search_tencentdb() 互补(腾讯云 vs 本地文件)

        Args:
            query: 查询关键词
            max_results: 最大结果数

        Returns:
            匹配的记忆条目列表
        """
        results = []
        memory_dir = Path.home() / ".openclaw" / "workspace" / "memory"
        if not memory_dir.exists():
            # 尝试备用路径
            memory_dir = Path.home() / ".openclaw/workspace/skills/yaoyao-memory-v2/memory"
            if not memory_dir.exists():
                return results

        query_lower = query.lower()
        query_words = set(query_lower.split())

        # 扫描 .md 文件(按修改时间倒序,最新的在前)
        md_files = sorted(memory_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)

        for fpath in md_files[:50]:  # 最多扫50个文件
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                content_lines = content.split("\n")

                # 对每个文件做关键词匹配
                score = 0
                matched_lines = []
                for line in content_lines:
                    line_lower = line.lower()
                    line_words = set(line_lower.split())
                    overlap = len(query_words & line_words)
                    if overlap > 0:
                        score += overlap * 10
                        if len(line.strip()) > 5:
                            matched_lines.append(line.strip()[:200])

                if score > 0 and matched_lines:
                    results.append({
                        "id": f"memory_file:{fpath.name}",
                        "content": "\n".join(matched_lines[:5]),
                        "confidence": min(score / 100.0, 0.95),
                        "source": "memory_files",
                        "metadata": {
                            "file": fpath.name,
                            "file_path": str(fpath),
                            "modified": datetime.fromtimestamp(fpath.stat().st_mtime).isoformat()
                        },
                        "score": score
                    })
            except Exception:
                continue

        # 按分数排序
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:max_results]

    # ==================== 自主任务集成(从 autonomous_integrator 融合)====================

    def _get_autonomous_integrator(self):
        """懒加载 AutonomousTasksIntegrator"""
        if not hasattr(self, '_auto_integrator') or self._auto_integrator is None:
            from autonomous_integrator import AutonomousTasksIntegrator
            self._auto_integrator = AutonomousTasksIntegrator()
        return self._auto_integrator

    def get_next_proactive_task(self) -> Optional[Dict]:
        """获取下一个主动任务 — Galaxy KoRa 增强"""
        task = self._get_autonomous_integrator().get_next_proactive_task()
        if not task:
            # 如果无主动任务，尝试 KoRa 模式匹配推荐
            patterns = self._get_kora_patterns()
            for p in patterns:
                if p.get('suggestion'):
                    task = {
                        'type': 'kora_pattern',
                        'title': p.get('scenario', '模式推荐'),
                        'description': p.get('suggestion', ''),
                        'source': 'kora',
                    }
                    break
        return task

    def _koRa_behavioral_model(self) -> Dict:
        """
        行为模式识别 — 统计用户 request 类型分布
        Galaxy KoRa: 基于历史请求计算用户行为画像
        返回当前 Session 的请求类型统计
        """
        if not self._kora_behavior_requests:
            return {"request_types": {}, "total": 0, "dominant_type": "unknown"}

        type_counts: Dict[str, int] = {}
        for r in self._kora_behavior_requests:
            t = r.get('type', 'unknown')
            type_counts[t] = type_counts.get(t, 0) + 1

        total = len(self._kora_behavior_requests)
        dominant = max(type_counts, key=type_counts.get) if type_counts else "unknown"

        return {
            "request_types": type_counts,
            "total": total,
            "dominant_type": dominant,
            "type_ratios": {k: round(v / total, 2) for k, v in type_counts.items()},
        }

    def _koRa_pattern_recognition(self) -> List[Dict]:
        """
        模式识别 — 检测重复场景/时间模式
        Galaxy KoRa: 从近期请求中识别重复出现的模式
        如：每天同一时间问天气、每周固定做备份等
        """
        if len(self._kora_behavior_requests) < 5:
            return []

        patterns = []
        recent = self._kora_behavior_requests[-50:]

        # 检测相同 intent 的高频出现
        from collections import Counter
        intent_counts = Counter(r.get('intent', '') for r in recent)
        for intent, count in intent_counts.most_common(5):
            if count >= 3 and intent:
                patterns.append({
                    'type': 'frequency',
                    'intent': intent,
                    'count': count,
                    'description': f"'{intent}' 出现了 {count} 次",
                    'suggestion': f"检测到 '{intent}' 高频出现，是否需要设为自动化？",
                })

        # 检测时序模式（同样的 intent 间隔固定）
        from collections import defaultdict
        intent_times = defaultdict(list)
        for r in recent:
            intent = r.get('intent', '')
            ts = r.get('timestamp', 0)
            if intent and ts:
                intent_times[intent].append(ts)

        for intent, timestamps in intent_times.items():
            if len(timestamps) >= 3:
                intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
                if intervals and max(intervals) - min(intervals) < 300:
                    avg_interval = sum(intervals) / len(intervals)
                    patterns.append({
                        'type': 'temporal',
                        'intent': intent,
                        'interval_seconds': round(avg_interval),
                        'description': f"'{intent}' 约每 {round(avg_interval)} 秒出现一次",
                        'suggestion': f"发现周期性任务 '{intent}'，是否转为定时任务？",
                    })

        self._kora_patterns = patterns
        return patterns

    def _get_kora_patterns(self) -> List[Dict]:
        """获取 KoRa 模式（带缓存刷新）"""
        import time as _rtime
        now = _rtime.time()
        if not self._kora_patterns or now - self._kora_last_refresh > self._kora_refresh_interval:
            self._kora_patterns = self._koRa_pattern_recognition()
            self._kora_last_refresh = now
        return self._kora_patterns

    def _kora_record_request(self, user_input: str, intent: str = "",
                              request_type: str = "query",
                              complexity: str = "general") -> None:
        """记录一次请求到 KoRa 行为模型"""
        import time as _rtime
        self._kora_behavior_requests.append({
            'type': request_type,
            'intent': intent,
            'complexity': complexity,
            'input_preview': user_input[:100],
            'timestamp': _rtime.time(),
        })
        if len(self._kora_behavior_requests) > self._kora_behavior_max:
            self._kora_behavior_requests = self._kora_behavior_requests[-self._kora_behavior_max:]

    def push_to_hiboard(self, title: str, content: str) -> bool:
        """推送到负一屏"""
        return self._get_autonomous_integrator().push_to_hiboard(title, content)

    def get_brain_entries(self, category: str = None) -> List[Dict]:
        """获取知识库条目"""
        return self._get_autonomous_integrator().get_brain_entries(category)

    # ==================== 全量集成(从 full_integration 融合)====================

    def _get_full_integration(self):
        """懒加载 FullIntegration"""
        if not hasattr(self, '_full_integration') or self._full_integration is None:
            from full_integration import FullIntegration
            self._full_integration = FullIntegration()
        return self._full_integration

    def smart_recall(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        """智能检索(CRAG + 混合检索)"""
        return self._get_full_integration().smart_recall(query, top_k)

    def smart_answer(self, query: str) -> Dict[str, Any]:
        """智能回答"""
        return self._get_full_integration().smart_answer(query)

    # ==================== 弹性系统集成(从 resilience_system 融合)====================

    def _get_resilience_system(self):
        """懒加载 ResilienceSystem"""
        if not hasattr(self, '_resilience') or self._resilience is None:
            from resilience_system import get_resilience_system
            self._resilience = get_resilience_system()
        return self._resilience

    def check_resilience(self) -> Dict[str, bool]:
        """检查各组件弹性状态"""
        return self._get_resilience_system().check_all()

    def get_health_report(self) -> Dict:
        """获取健康报告"""
        return self._get_resilience_system().get_health_report()

    def get_degradation_level(self) -> tuple:
        """获取降级级别"""
        return self._get_resilience_system().get_degradation_level()

    # ==================== 完整恢复集成(从 full_recovery 融合)====================

    def check_recovery_status(self) -> Dict:
        """检查恢复状态"""
        from full_recovery import check_status
        return check_status()

    # ==================== 增强检索集成(从 XiaoyiMemoryV2 enhanced_* 融合,走统一API)====================

    def enhanced_recall(self, query: str, top_k: int = 10, use_crag: bool = True) -> Dict:
        """增强检索(通过 XiaoYiClawLLM recall + 本地增强)"""
        memories = self.recall(query, top_k=top_k)
        return {
            "basic_results": memories,
            "total_results": len(memories),
            "cache_hit": False
        }

    def fast_generate(self, query: str, top_k: int = 3) -> Dict:
        """快速生成 — Flash 闪电回答（替代投机解码）

        Recall + Flash 合成，不需要 embedding / asyncio / NIM。
        """
        try:
            memories = self.recall(query, top_k=top_k)
            context = "\n\n".join([
                r.get('content', r.get('user_text', ''))[:800]
                for r in (memories or [])
            ])
            if self.llm_flash:
                flash_resp = self.llm_flash.chat.completions.create(
                    model=self._llm_flash_model,
                    messages=[{"role": "user",
                        "content": f"请根据以下信息回答用户问题，简洁准确:\n\n{context[:3000]}\n\n用户问题: {query}"}],
                    max_tokens=512, temperature=0.3,
                )
                answer = flash_resp.choices[0].message.content.strip()
                return {"answer": answer, "latency_ms": 0, "confidence": 0.7}
        except Exception as e:
            logger.warning(f"fast_generate flash fallback: {e}")

        # 纯兜底
        answer_data = self.answer(query, top_k=top_k)
        return {
            "answer": answer_data.get("answer", ""),
            "latency_ms": 0,
            "confidence": answer_data.get("confidence", 0)
        }

    def smart_cache(self, content: str, metadata: Dict = None) -> Dict:
        """智能缓存"""
        memory_id = self.remember(content, metadata=metadata)
        return {
            "memory_id": memory_id,
            "cached": True
        }

    # ==================== R-CCAM 结构化认知循环 ====================

    class PhaseState:
        """R-CCAM 五阶段的状态传递对象"""

        def __init__(self, user_input: str):
            # 原始输入
            self.user_input = user_input
            self.session_key = f"rccam_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

            # 图片相关(Visual RAG)
            self.has_image: bool = False
            self.image_source: Optional[str] = None

            # Retrieval 阶段输出
            self.retrieved_memories: List[Dict] = []
            self.dag_summaries: List[Dict] = []
            self.kg_entities: List[Dict] = []
            self.web_results: List[Dict] = []
            self.retrieval_confidence: float = 0.0
            self.needs_more_info: bool = False
            self.paper_engine_results: List[Dict] = []  # RAPTOR/GraphRAG/Reflection
            self.suggested_tool: Optional[Dict] = None  # Toolformer 预判工具

            # Cognition 阶段输出
            self.knowledge_type: str = "info"
            self.type_confidence: float = 0.5
            self.analysis: Dict[str, Any] = {}
            self.intent: str = "query"
            self.thinking_skills_used: List[str] = []

            # Control 阶段输出
            self.strategy: str = "answer"
            self.boundaries: List[str] = []
            self.fallback: str = "polite_refuse"
            self.reasoning: str = ""
            self.control_decision: Dict[str, Any] = {}

            # Action 阶段输出
            self.action_result: Any = None
            self.action_success: bool = False
            self.action_error: Optional[str] = None
            self.generated_answer: str = ""
            self.answer_confidence: float = 0.0

            # Memory 阶段输出
            self.memory_ids: List[str] = []
            self.dag_nodes_created: int = 0
            self.synapse_updated: bool = False
            self.emotion_marked: bool = False
            self.evolution_triggered: bool = False

            # 循环控制
            self.cycle_count: int = 0
            self.max_cycles: int = 3
            self.should_stop: bool = False
            self.stop_reason: str = ""
            self.previous_cycle_id: str = ""

            # ═══ 透明直播日志（供 AI 主会话实时查看） ═══
            self.phase_logs: List[Dict] = []

            # ═══ 元认知领域（新增，MedCoG + Meta-R1 融合） ═══
            self.meta_complexity: float = 0.5
            self.meta_familiarity: float = 0.5
            self.meta_knowledge_density: float = 0.5
            self.meta_strategy: str = "full_pipeline"
            self.meta_reasoning_budget: int = 1
            self.meta_retrieval_depth: int = 8
            self.meta_exploration_width: int = 1
            self.meta_need_refine: bool = False
            self.meta_online_monitor: list = []
            self.meta_early_stop: bool = False
            self.meta_stop_reason: str = ""
            self._online_errors: list = []
            self._current_cycle_plan: dict = {}
            # ═══ 元认知结束 ═══

    def _retrieval_phase(self, state: 'PhaseState', custom_query: str = None) -> 'PhaseState':
        """
        R-CCAM 阶段 1: Retrieval(检索阶段)

        通过 retrieval_hub 统一入口,五路并行检索后 RRF 融合:
        1. yaoyao_bridge(FTS5 + 向量 + reranker)
        2. DAG 上下文(scene_trace + GRAVITY)
        3. 突触巩固引擎(突触网络 + 艾宾浩斯)
        4. 论文引擎(RAPTOR + GraphRAG + Reflection)
        5. 联网搜索(xiaoyi-web-search,低置信度时自动触发)
        """
        query = custom_query or state.user_input

        try:
            from retrieval_hub import retrieval_hub
            hub_result = retrieval_hub(query, top_k=8)

            memories = hub_result.get("results", [])
            stats = hub_result.get("stats", {})

            state.retrieved_memories = memories
            state.retrieval_confidence = stats.get("confidence", 0)

            # 分离各来源的结果供后续阶段使用
            if stats.get("paper", 0) > 0:
                state.paper_engine_results = [
                    r for r in memories if 'paper' in r.get('source', '')
                ]
            if stats.get("web", 0) > 0:
                state.web_results = [
                    r for r in memories if r.get('source') == 'web'
                ]

            state.needs_more_info = stats.get("confidence", 0) < 0.3

            logger.info(f"Retrieval phase: confidence={state.retrieval_confidence:.2f}, "
                       f"sources: local={stats.get('local',0)} dag={stats.get('dag',0)} "
                       f"synapse={stats.get('synapse',0)} paper={stats.get('paper',0)} "
                       f"web={stats.get('web',0)} ({stats.get('time_ms',0)}ms)")

        except ImportError:
            # fallback: 旧版逻辑
            self._legacy_retrieval(state, query)
        except Exception as e:
            logger.warning(f"Retrieval phase failed: {e}")
            self._legacy_retrieval(state, query)

        return state

    def _legacy_retrieval(self, state: 'PhaseState', query: str):
        """retrieval_hub 不可用时的降级检索"""
        try:
            memories = self.recall(query, top_k=5)
            if memories:
                state.retrieved_memories = memories
                if isinstance(memories, list):
                    state.retrieval_confidence = max(
                        (m.get('score', 0) for m in memories), default=0
                    )
        except Exception as e:
            logger.warning(f"Legacy recall failed: {e}")

        try:
            summaries = self._load_dag_summaries(top_k=3)
            if summaries:
                state.dag_summaries = summaries
        except Exception as e:
            logger.warning(f"Legacy DAG load failed: {e}")

        state.needs_more_info = (
            state.retrieval_confidence < 0.3 and
            not state.dag_summaries
        )

    def _cognition_phase(self, state: 'PhaseState') -> 'PhaseState':
        """
        R-CCAM 阶段 2: Cognition(认知阶段)

        使用 IntelligentThinkingTrigger 替代手搓关键词分类。
        - 分析复杂度 / 问题类型 / 困惑程度
        - 自动推荐思考技能
        """
        query = state.user_input

        # 用 IntelligentThinkingTrigger 做智能分类
        try:
            from intelligent_thinking_trigger import IntelligentThinkingTrigger, ThinkingSkill
            trigger = IntelligentThinkingTrigger()
            analysis = trigger.detect_thinking_need(query)

            state.knowledge_type = analysis.question_type
            state.type_confidence = analysis.confidence
            state.analysis = {
                "complexity": analysis.complexity,
                "confusion": analysis.confusion_level,
                "reasoning": analysis.reasoning,
            }

            # 映射建议技能到中文名 + 读取 SKILL.md 上下文注入
            skill_name_map = {
                # 思考技能 (9)
                ThinkingSkill.FIRST_PRINCIPLES: "第一性原理",
                ThinkingSkill.SYSTEMS_THINKING: "系统思维",
                ThinkingSkill.CRITICAL_THINKING: "批判性思维",
                ThinkingSkill.BACKWARD_THINKING: "逆向思维",
                ThinkingSkill.ANALOGICAL_THINKING: "类比思维",
                ThinkingSkill.FEYNMAN_TECHNIQUE: "费曼技巧",
                ThinkingSkill.DECISION_ENGINE: "决策引擎",
                ThinkingSkill.PRODUCT_THINKING: "产品思维",
                # Matt Pocock 工程技能
                ThinkingSkill.DIAGNOSE: "diagnose (调查研究)",
                ThinkingSkill.GRILL_WITH_DOCS: "grill-with-docs (矛盾分析)",
                ThinkingSkill.TDD: "tdd (批评与自我批评)",
                ThinkingSkill.IMPROVE_ARCH: "improve-codebase-architecture (实践认知)",
                ThinkingSkill.PROTOTYPE: "prototype (集中兵力)",
                ThinkingSkill.ZOOM_OUT: "zoom-out (统筹全局)",
                ThinkingSkill.GRILL_ME: "grill-me (武装思想)",
                ThinkingSkill.CAVEMAN: "caveman",
                ThinkingSkill.HANDOFF: "handoff",
                ThinkingSkill.WRITE_SKILL: "write-a-skill",
                # 方法论 (qiushi 11)
                ThinkingSkill.INVESTIGATION_FIRST: "调查研究",
                ThinkingSkill.CONTRADICTION_ANALYSIS: "矛盾分析",
                ThinkingSkill.PRACTICE_COGNITION: "实践认知",
                ThinkingSkill.CONCENTRATE_FORCES: "集中兵力",
                ThinkingSkill.OVERALL_PLANNING: "统筹全局",
                ThinkingSkill.MASS_LINE: "群众路线",
                ThinkingSkill.CRITICISM_SELF_CRITICISM: "批评与自我批评",
                ThinkingSkill.SPARK_PRAIRIE_FIRE: "星星之火",
                ThinkingSkill.ARMING_THOUGHT: "武装思想",
                ThinkingSkill.PROTRACTED_STRATEGY: "持久战",
                ThinkingSkill.WORKFLOWS: "工作流",
                ThinkingSkill.NONE: None,
            }
            suggested = skill_name_map.get(analysis.suggested_skill)
            state.thinking_skills_used = [suggested] if suggested else []

            # 读取 SKILL.md 注入到 analysis 上下文(所有已定义的技能都支持)
            if suggested and analysis.suggested_skill != ThinkingSkill.NONE:
                skill_path = None
                try:
                    # 优先从 ALL_SKILL_PATHS 查找
                    skill_path = trigger.ALL_SKILL_PATHS.get(analysis.suggested_skill)
                except AttributeError:
                    # 降级 MATT_POCOCK_SKILL_PATHS
                    try:
                        skill_path = trigger.MATT_POCOCK_SKILL_PATHS.get(analysis.suggested_skill)
                    except AttributeError:
                        pass
                if skill_path:
                    ws_path = getattr(state, 'workspace_path', os.path.expanduser('~/.openclaw/workspace'))
                    full_path = f"{ws_path}/skills/{skill_path}"
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            skill_md = f.read()
                            # 取前 500 个字作为技能说明注入到 analysis
                            state.analysis['skill_guide'] = skill_md[:500]
                    except Exception:
                        pass

            # ── 将重放缓冲区注入 skill_guide(与方法论技能并列)──
            replay = state.analysis.get('replay_buffer', '')
            if replay:
                existing = state.analysis.get('skill_guide', '')
                state.analysis['skill_guide'] = (
                    (existing + '\n\n' if existing else '') +
                    f'【已验证记忆参考】\n{replay}'
                )

        except Exception as e:
            logger.warning(f"Cognition phase - IntelligentThinkingTrigger failed, fallback: {e}")
            # 降级到简单关键词分类
            try:
                classification = self.classify_knowledge(query)
                state.knowledge_type = classification.get('type', 'info')
                state.type_confidence = classification.get('confidence', 0.5)
            except Exception:
                pass

            # 降级意图分析
            state.thinking_skills_used = self._select_thinking_skills(
                state.knowledge_type, state._analyze_intent(query)
            )

        # ── SkillCoordinator 增强：补充推荐思考技能（与 IntelligentThinkingTrigger 并行） ──
        try:
            if hasattr(self, 'skill_coordinator') and self.skill_coordinator:
                sc_skill = self.skill_coordinator.recommend_skill(query)
                sc_combo = self.skill_coordinator.suggest_combination(query)
                if sc_skill and sc_skill not in state.thinking_skills_used:
                    state.thinking_skills_used.append(sc_skill)
                    logger.debug(f"SkillCoordinator 补充推荐: {sc_skill}")
                state.analysis['skill_coordinator_combo'] = sc_combo
        except Exception as e:
            logger.debug(f"SkillCoordinator 调用失败: {e}")

        # 检测需要实时信息的查询(强制走联网搜索)
        realtime_keywords = [
            "天气", "今天天气", "温度", "下雨", "台风", "气象",
            "新闻", "最新", "今天", "实时",
            "weather", "temperature", "rain", "forecast",
        ]
        query = getattr(state, 'user_input', '')
        if any(kw in query.lower() for kw in realtime_keywords):
            state.needs_more_info = True
            logger.debug(f"实时信息查询,强制 needs_more_info=True")

        # ── Visual RAG: 图片/OCR 自动路由(Visual RAG 论文方案)──
        try:
            visual_keywords = [
                "这张图", "图片", "截图", "屏幕", "拍摄", "拍照", "照片",
                "识别", "看到", "图中", "图像", "海报", "表格", "文档",
                "这张", "看看这个", "看下", "帮我看看",
            ]
            has_image_msg = hasattr(state, 'has_image') and state.has_image
            has_media = any(kw in query.lower() for kw in visual_keywords)

            if (has_image_msg or has_media) and self.ocr2:
                # 调用 OCR2 提取图片文字/结构化数据
                from deepseek_ocr2_adapter import ImageUnderstandingMode, get_adapter
                ocr2 = get_adapter()

                # 尝试从 state 获取图片路径
                image_source = getattr(state, 'image_source', None) or query

                # 确定 OCR 模式:含"表格/图表"关键词走 CHART,否则走 OCR
                ocr_mode = ImageUnderstandingMode.OCR
                if any(kw in query.lower() for kw in ["表格", "图表", "柱状", "折线", "饼图", "数据"]):
                    ocr_mode = ImageUnderstandingMode.CHART
                elif any(kw in query.lower() for kw in ["文档", "海报", "截图", "文章"]):
                    ocr_mode = ImageUnderstandingMode.DOCUMENT

                result = ocr2.understand(image_source, prompt=query[:200], mode=ocr_mode)

                if result and result.get('content'):
                    visual_context = f"[OCR2提取] {result['content'][:1000]}"
                    if state.analysis.get('skill_guide'):
                        state.analysis['skill_guide'] += f"\n\n{visual_context}"
                    else:
                        state.analysis['skill_guide'] = visual_context
                    state.analysis['visual_rag_used'] = True
                    state.analysis['ocr_mode'] = str(ocr_mode) if not isinstance(ocr_mode, str) else ocr_mode
                    logger.info(f"Visual RAG: OCR2 提取成功 ({len(result['content'])} 字符)")
        except Exception as e:
            logger.warning(f"Visual RAG OCR2 调用失败: {e}")

        # ── L1 入场保护:自动注入人格摘要 ──
        # 不依赖 OpenClaw hook,从 persona 文件自读
        try:
            if not state.analysis.get('persona_injected'):
                persona_paths = [
                    os.path.join(os.path.expanduser("~/.openclaw/workspace"), "persona.md"),
                    os.path.join(os.path.expanduser("~/.openclaw/workspace"), "SOUL.md"),
                    os.path.join(os.path.expanduser("~/.openclaw/workspace"), "IDENTITY.md"),
                ]
                persona_parts = []
                for p in persona_paths:
                    if os.path.exists(p):
                        with open(p, "r", encoding="utf-8") as f:
                            content = f.read(1500)
                            name = os.path.basename(p).replace(".md", "")
                            persona_parts.append(f"【{name}】\n{content.strip()[:1200]}")

                if persona_parts:
                    persona_text = "\n\n".join(persona_parts)
                    existing = state.analysis.get('skill_guide', '')
                    state.analysis['skill_guide'] = (
                        f"【人格上下文】\n{persona_text[:2000]}\n\n" +
                        (existing if existing else '')
                    )
                    state.analysis['persona_injected'] = True
                    logger.info("L1: 人格摘要已注入 Cognition 阶段")
        except Exception as e:
            logger.warning(f"L1 persona injection failed: {e}")

        # ── DAG 当前会话上下文注入(紧接在人格之后)──
        try:
            dag_ctx = state.analysis.get('current_dag_context', '')
            if dag_ctx:
                existing = state.analysis.get('skill_guide', '')
                if existing:
                    state.analysis['skill_guide'] = existing + f"\n\n{dag_ctx}"
                else:
                    state.analysis['skill_guide'] = dag_ctx
                logger.info(f"DAG 上下文已注入 skill_guide ({len(dag_ctx)} 字符)")
        except Exception as e:
            logger.warning(f"DAG context injection failed: {e}")

        return state

    def _analyze_intent(self, query: str) -> str:
        """分析用户意图(降级用)"""
        intent_patterns = {
            "query": ["什么", "怎么", "为什么", "如何", "谁是", "哪里"],
            "action": ["帮我", "请", "做", "创建", "生成", "写", "画", "调"],
            "confirm": ["确认", "确定", "是这", "对吗", "是不是"],
            "clarify": ["详细", "解释", "说明", "举例"],
            "memory": ["记住", "回忆", "忘了", "之前"],
            "command": ["执行", "运行", "启动", "停止", "配置"],
        }
        for intent, keywords in intent_patterns.items():
            if any(kw in query for kw in keywords):
                return intent
        return "query"

    def _select_thinking_skills(self, knowledge_type: str, intent: str) -> List[str]:
        """基于问题类型选择思考技能(降级用)"""
        mapping = {
            "decision": ["矛盾分析", "决策引擎", "系统思维"],
            "error": ["批判性思维", "调查研究", "第一性原理"],
            "learning": ["费曼技巧", "类比思维", "实践认知"],
            "task": ["集中兵力", "统筹全局", "持久战"],
            "preference": ["产品思维", "群众路线"],
            "progress": ["批评与自我批评", "工作流"],
            "correction": ["调查研究", "批判性思维", "自我批评"],
            "info": ["系统思维", "调查研究"],
        }
        return mapping.get(knowledge_type, ["调查研究"])

    def _meta_regulator(self, state: 'PhaseState') -> 'PhaseState':
        """
        元认知调节器（MedCoG + Meta-R1 融合设计）

        读取 Cognition 产出的三维评估（complexity / familiarity / knowledge_density），
        动态决定策略路径、推理预算、检索深度、探索宽度。
        """
        c = state.meta_complexity
        f = state.meta_familiarity
        k = state.meta_knowledge_density

        # ── 先查护栏 ──
        bc = self._rails_check_boundary(state.user_input)
        is_violation = bc.get("violation", False)
        if is_violation:
            state.meta_strategy = "boundary_violation"
            state.meta_reasoning_budget = 0
            state.meta_retrieval_depth = 0
            state.meta_exploration_width = 1
            state.meta_need_refine = False
            state.control_decision = {
                "strategy": "boundary_violation",
                "fallback": "polite_refuse",
                "is_violation": True,
                "violation_reason": bc.get("reason", ""),
                "violation_detail": bc.get("detail", ""),
                "reasoning": f"边界违反: {bc.get('reason', '')}",
            }
            state.boundaries = [
                "不泄露 API Key、Token、系统配置路径",
                "不执行可能破坏文件系统或服务的命令",
                "不确定的信息必须注明来源或说明不确定性",
                "不冒充其他系统或用户身份",
                "不执行未授权的网络爬取或数据导出",
            ]
            return state

        _LOW = 0.35
        _HIGH = 0.65

        if c < _LOW and f >= _LOW:
            meta_strategy = "direct_answer"
            budget = 0; depth = 0; width = 1; refine = False
            reasoning = f"简单(c={c:.2f})+熟悉(f={f:.2f}) → 直答"
        elif c < _LOW and f < _LOW:
            meta_strategy = "light_retrieval"
            budget = 0; depth = 4; width = 1; refine = False
            reasoning = f"简单(c={c:.2f})+陌生(f={f:.2f}) → 轻量检索"
        elif c >= _LOW and f >= _HIGH:
            meta_strategy = "deep_reasoning"
            budget = 1; depth = 0; width = 3; refine = True
            reasoning = f"复杂(c={c:.2f})+熟悉(f={f:.2f}) → 深度推理+MultiPath"
        elif c >= _LOW and f < _HIGH and k >= _LOW:
            meta_strategy = "full_pipeline"
            budget = 2; depth = 12; width = 3; refine = True
            reasoning = f"复杂(c={c:.2f})+陌生(f={f:.2f})+知识(k={k:.2f}) → 完整管线+深检索"
        else:
            meta_strategy = "full_pipeline"
            budget = 1; depth = 8; width = 1; refine = False
            reasoning = f"标准情况(c={c:.2f},f={f:.2f},k={k:.2f}) → 标准管线"

        state.meta_strategy = meta_strategy
        state.meta_reasoning_budget = budget
        state.meta_retrieval_depth = depth
        state.meta_exploration_width = width
        state.meta_need_refine = refine

        strategy_map = {
            "direct_answer": "answer", "light_retrieval": "info_insufficient",
            "full_pipeline": "answer", "deep_reasoning": "answer",
            "boundary_violation": "boundary_violation",
        }
        state.strategy = strategy_map.get(meta_strategy, "answer")
        fallback_map = {
            "direct_answer": "polite_refuse", "light_retrieval": "answer_with_uncertainty",
            "full_pipeline": "answer_with_uncertainty", "deep_reasoning": "polite_refuse",
            "boundary_violation": "polite_refuse",
        }
        state.fallback = fallback_map.get(meta_strategy, "polite_refuse")

        state.boundaries = [
            "不泄露 API Key、Token、系统配置路径",
            "不执行可能破坏文件系统或服务的命令",
            "不确定的信息必须注明来源或说明不确定性",
            "不冒充其他系统或用户身份",
            "不执行未授权的网络爬取或数据导出",
        ]

        state.control_decision = {
            "strategy": state.strategy, "meta_strategy": meta_strategy,
            "fallback": state.fallback, "is_violation": False,
            "violation_reason": "", "violation_detail": "",
            "reasoning": reasoning,
            "meta": {
                "complexity": round(c, 2), "familiarity": round(f, 2),
                "knowledge_density": round(k, 2), "budget": budget,
                "depth": depth, "width": width, "refine": refine,
            }
        }
        logger.info(f"[元认知] {reasoning}")
        return state

    def _merge_gate(self, state: 'PhaseState') -> 'PhaseState':
        """
        L5 Merge Gate: 多路检索 + 认知合并门控

        在 _meta_regulator 之前执行，将 R 阶段五路检索结果与 C 阶段认知分析
        合并、去重、交叉验证，输出 merged_context 并微调元认知参数。
        """
        merged = []
        seen_sigs = set()

        # 1. 收集所有检索来源
        all_sources = [
            ("记忆", state.retrieved_memories or [], "content"),
            ("DAG", state.dag_summaries or [], "content"),
            ("知识图谱", state.kg_entities or [], "name"),
            ("联网", state.web_results or [], "content"),
            ("论文引擎", state.paper_engine_results or [], "content"),
        ]
        for label, items, key in all_sources:
            for item in items:
                text = (item.get(key, "") or "")[:400]
                if not text:
                    continue
                sig = text[:50]  # 前 50 字符去重
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)
                merged.append({"source": label, "content": text})

        # 2. 写入合并上下文（最多 12 条）
        if merged:
            context_lines = []
            for m in merged[:12]:
                context_lines.append(f"[{m['source']}] {m['content']}")
            state.analysis['merged_context'] = "\n".join(context_lines)
        else:
            state.analysis['merged_context'] = ""

        # 3. 微调元认知参数（检索充实度修正）
        retrieval_count = len(merged)
        source_types = set(m['source'] for m in merged)

        # 检索量多 → knowledge_density 升高
        if retrieval_count >= 8:
            state.meta_knowledge_density = min(1.0, state.meta_knowledge_density + 0.15)
        elif retrieval_count >= 4:
            state.meta_knowledge_density = min(1.0, state.meta_knowledge_density + 0.08)
        elif retrieval_count <= 1:
            state.meta_knowledge_density = max(0.1, state.meta_knowledge_density - 0.1)

        # 来源多样 → familiarity 微降（更多来源需更多判断）
        if len(source_types) >= 3:
            state.meta_familiarity = max(0.1, state.meta_familiarity - 0.08)

        state.analysis['merged_count'] = retrieval_count
        state.analysis['merged_sources'] = list(source_types)

        return state

    def _rails_check_analysis(self, state: 'PhaseState') -> dict:
        """
        L5 护栏增强版: 检索结果 + 认知分析内容安全检查

        在原有 user_input 边界检查之上，额外检查 R 阶段检索结果和
        C 阶段认知分析中是否包含敏感信息或违规建议。
        """
        # 1. 检索结果中的敏感凭据
        for mem in (state.retrieved_memories or []):
            text = json.dumps(mem.get("content", ""), ensure_ascii=False).lower()
            if any(kw in text for kw in [
                "sk-ea", "sk-pz", "api_key", "apikey", "api密钥",
                "token:", "authorization:", "bearer ",
            ]):
                return {"violation": True, "reason": "检索结果含敏感凭据，已屏蔽",
                        "category": "memory_leak", "detail": "retrieved_memories"}

        # 2. DAG 上下文中的系统路径泄露
        for dag in (state.dag_summaries or []):
            text = json.dumps(dag.get("content", ""), ensure_ascii=False).lower()
            if any(kw in text for kw in [
                "/home/sandbox", "/root/.ssh", "/etc/shadow",
                "~/.openclaw", "OPENCLAW_WORKSPACE",
            ]):
                return {"violation": True, "reason": "DAG 上下文含系统路径，已过滤",
                        "category": "path_leak", "detail": "dag_summaries"}

        # 3. 认知分析是否建议违规操作
        ana = state.analysis or {}
        cog_text = " ".join([
            str(ana.get("skill_guide", "")),
            str(ana.get("reasoning", "")),
            str(ana.get("flash_summary", "")),
        ]).lower()
        if any(kw in cog_text for kw in [
            "删除所有记忆", "清空数据库", "rm -rf", "格式化",
            "导出全部", "批量导出", "kill -9", "reboot", "shutdown",
        ]):
            return {"violation": True, "reason": "认知分析建议违规操作，已阻断",
                    "category": "cognition_violation", "detail": "analysis"}

        return {"violation": False, "reason": "", "category": "", "detail": ""}

    def _control_phase(self, state: 'PhaseState') -> 'PhaseState':
        """
        R-CCAM 阶段 3: Control(控制阶段) — Merge Gate + Rails + 元认知

        执行顺序:
          1. Merge Gate — 合并 R + C 结果，微调元认知参数
          2. Rails 升级版 — 检索 + 认知分析安全检查
          3. Rails 原版 — user_input 边界检查
          4. _meta_regulator — 元认知决策
        """
        # 步骤 1: Merge Gate
        state = self._merge_gate(state)

        # 步骤 2: Rails 升级版（检索 + 认知安全检查）
        bc2 = self._rails_check_analysis(state)
        if bc2.get("violation", False):
            state.meta_strategy = "boundary_violation"
            state.meta_reasoning_budget = 0
            state.meta_retrieval_depth = 0
            state.meta_exploration_width = 1
            state.meta_need_refine = False
            state.control_decision = {
                "strategy": "boundary_violation",
                "fallback": "polite_refuse",
                "is_violation": True,
                "violation_reason": bc2.get("reason", ""),
                "violation_detail": bc2.get("detail", ""),
                "reasoning": f"检索/认知违规: {bc2.get('reason', '')}",
            }
            return state

        state = self._meta_regulator(state)
        return state

    def _rails_check_boundary(self, query: str) -> dict:
        """
        L5 护栏:多维度边界检查(替代简单关键词匹配)

        覆盖:记忆操作、系统配置、外部操作、身份冒充、危险指令。
        """
        q = query.lower().strip()

        rules = [
            (["删除记忆", "清空记忆", "清空所有记忆", "全部删除", "清除记忆", "删除所有"],
             "memory_delete", "批量记忆删除操作需要确认"),
            (["api key", "api密钥", "api_key", "apikey", "系统密码", "root密码", "root权限", "sudo权限",
              "查看token", "查看apikey", "导出key"],
             "system_secrets", "禁止泄露系统秘密"),
            (["rm -rf", "格式化", "fdisk", "dd if", "chmod 777", "chown -r",
              "kill -9", "systemctl stop", "service stop", "reboot", "shutdown -h"],
             "dangerous_command", "可能破坏系统的操作需要确认"),
            (["冒充我", "替我说话", "以我的名义", "代我发言", "假装是我", "扮演我"],
             "identity_spoof", "禁止冒充用户身份"),
            (["导出全部数据", "导出所有", "批量导出", "爬取所有", "抓取全部"],
             "data_export", "大量数据导出需要确认"),
            (["调用外部api", "调用第三方", "访问外部系统", "扫描端口", "端口扫描"],
             "external_call", "外部调用需确认"),
        ]

        for keywords, category, default_reason in rules:
            if any(kw in q for kw in keywords):
                return {
                    "violation": True,
                    "reason": default_reason,
                    "category": category,
                    "detail": f"触犯边界规则: {category}",
                }

        # Privacy Gate: Galaxy 三级隐私分级
        _privacy_level = self._rails_privacy_gate(query)
        return {
            "violation": False,
            "reason": "",
            "category": "",
            "detail": "",
            "privacy_level": _privacy_level,
        }

    def _rails_privacy_gate(self, query: str) -> str:
        """Galaxy Privacy Gate: 三级隐私分级
        Level 0 (LOCAL) — 敏感个人信息,不进云端
        Level 1 (SANITIZED) — 脱敏后可用云端
        Level 2 (PUBLIC) — 任意模型
        """
        q = query.lower().strip()
        import re as _re

        # Level 0: 身份证/手机/银行卡/token 模式
        if _re.search(r'\\d{17}[\\dXx]|\\d{15}', q) or            _re.search(r'1[3-9]\d{9}', q) or            _re.search(r'\d{16,19}', q) or            _re.search(r'(sk-|pk-)[a-zA-Z0-9]{20,}', q):
            return "local"

        _p0_kw = ['我的身份证','我的手机号','我的银行卡','我的密码',
                  '我的地址','我的住址','我的家庭','我的工资','我的社保']
        if any(k in q for k in _p0_kw):
            return "local"

        # Level 1: 含"我的"等隐私线索
        _p1_kw = ['我的','我家','我公司','我学校','我的位置','我住在']
        if any(k in q for k in _p1_kw):
            return "sanitized"

        return "public"

    def _process_meta_trend(self, state: 'PhaseState'):
        """消息级元认知: 每次 process 后记复杂度/熟悉度趋势,异常波动触发反思"""
        _path = os.path.join(WORKSPACE, '.learnings', 'meta_trend.json') if WORKSPACE else ''
        if not _path:
            return
        import time as _rt
        _entry = {'ts': _rt.time(),
                  'complexity': state.meta_complexity,
                  'familiarity': state.meta_familiarity,
                  'knowledge_density': state.meta_knowledge_density,
                  'strategy': state.meta_strategy}
        try:
            os.makedirs(os.path.dirname(_path), exist_ok=True)
            _data = []
            if os.path.exists(_path):
                with open(_path) as _f:
                    _data = json.load(_f)
            _data.append(_entry)
            _data = _data[-30:]
            with open(_path, 'w') as _f:
                json.dump(_data, _f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # 异常波动检测: 连续3次同方向剧烈波动
        if len(_data) >= 3:
            _recent = _data[-3:]
            _c = [t['complexity'] for t in _recent]
            _f = [t['familiarity'] for t in _recent]
            _anomaly = (_c[-1] < 0.2 and _c[-2] - _c[-1] > 0.3) or                        (_f[-1] < 0.2 and _f[-2] - _f[-1] > 0.3)
            if _anomaly:
                state.analysis['meta_anomaly_detected'] = True
                _cnt = sum(1 for t in _data[-10:] if t.get('complexity',0.5) < 0.2)
                if _cnt >= 3 and state.cycle_count > 0 and self.llm_flash:
                    try:
                        from thinking_enhanced import ThinkingEnhanced
                        _te = ThinkingEnhanced(self.llm_flash)
                        _r = _te.introspect(max_samples=10)
                        if _r.get("success"):
                            state.analysis['self_evolution'] = _r
                            state.evolution_triggered = True
                    except Exception:
                        pass

    def _action_phase(self, state: 'PhaseState') -> 'PhaseState':
        """
        L5: R-CCAM 阶段 4: Action(行动阶段)

        执行控制阶段的决定。边界拒绝时给出具体原因。
        """
        query = state.user_input

        if state.strategy == "boundary_violation":
            state.action_success = True
            reason = state.control_decision.get("violation_reason", "")
            detail = state.control_decision.get("violation_detail", "")
            if reason:
                state.generated_answer = f"抱歉,{reason}。"
            else:
                state.generated_answer = "抱歉,这个请求超出了我的处理范围。"
            state.answer_confidence = 1.0
            return state

        if state.strategy == "info_insufficient":
            # 多源联网搜索补充（不生成最终回答，交由主模型）
            query = state.user_input
            state.answer_confidence = 0.0
            state.analysis['search_results'] = ""
            state.analysis['user_location'] = ""

            # 加载用户位置
            _user_loc = ""
            _user_pinyin = ""
            try:
                _loc_path = os.path.join(WORKSPACE, "user_location.json") if WORKSPACE else ""
                if _loc_path and os.path.exists(_loc_path):
                    with open(_loc_path, "r") as _f:
                        _loc = json.load(_f)
                    _city = _loc.get("city", "")
                    _prov = _loc.get("province", "")
                    _country = _loc.get("country", "")
                    if _city:
                        _user_loc = f"{_prov or _country}{_city}" if _prov else _city
                    _user_pinyin = _loc.get("pinyin", "") or _city
                    state.analysis['user_location'] = _user_loc
            except Exception:
                pass

            try:
                # 1. 天气查询 — 自动注入位置到 wttr.in
                weather_keywords = ["天气", "温度", "下雨", "刮风", "雾", "雪", "晴",
                                    "weather", "temperature", "rain", "wind", "forecast"]
                is_weather = any(kw in query.lower() for kw in weather_keywords)

                if is_weather:
                    weather_data = ""
                    try:
                        import urllib.request, urllib.parse
                        _w_query = f"{_user_loc}{query}" if _user_loc else query
                        encoded_query = urllib.parse.quote(_w_query)
                        # wttr.in 支持城市名直接查
                        url = f"https://wttr.in/{encoded_query}?format=4&lang=zh"
                        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.88.1"})
                        with urllib.request.urlopen(req, timeout=8) as resp:
                            weather_data = resp.read().decode("utf-8").strip()
                        if weather_data and "Unknown" not in weather_data:
                            state.analysis['weather_data'] = weather_data
                            logger.debug(f"天气查询成功: {weather_data}")
                        else:
                            # 试拼音
                            _w_query_py = _user_pinyin or _city or ""
                            if _w_query_py:
                                url = f"https://wttr.in/{urllib.parse.quote(_w_query_py)}?format=4&lang=zh"
                                req = urllib.request.Request(url, headers={"User-Agent": "curl/7.88.1"})
                                with urllib.request.urlopen(req, timeout=8) as resp:
                                    weather_data = resp.read().decode("utf-8").strip()
                                if weather_data and "Unknown" not in weather_data:
                                    state.analysis['weather_data'] = weather_data
                    except Exception as we:
                        logger.debug(f"天气查询失败: {we}")

                # 2. xiaoyi-web-search — 注入位置到搜索关键词
                try:
                    import subprocess
                    _search_query = f"{_user_loc} {query}" if _user_loc else query
                    search_script = os.path.expanduser("~/.openclaw/workspace/skills/xiaoyi-web-search/scripts/search.js")
                    if os.path.exists(search_script):
                        result = subprocess.run(
                            ["node", search_script, _search_query, "-n", "3"],
                            capture_output=True, text=True, timeout=15,
                            cwd=os.path.expanduser("~/.openclaw/workspace/skills/xiaoyi-web-search")
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            state.analysis['search_results'] = result.stdout.strip()[:3000]
                except Exception as we:
                    logger.debug(f"web search 失败: {we}")

            except Exception as e:
                logger.debug(f"联网搜索补充失败: {e}")

            # 空 answer，由主模型根据 payload 组织回答
            state.generated_answer = ""
            state.action_success = True
            state.analysis['action_delegated'] = True
            return state

        if state.strategy == "clarify_needed":
            state.generated_answer = (
                "你提到的这个问题我理解得还不太确切,"
                "可以再说具体一点吗?"
            )
            state.answer_confidence = 0.2
            state.action_success = True
            return state

        # 默认: 走 SmartProcessor 统一路由（替代内联 Flash/Pro 调用）
        _persona = state.analysis.get('persona_context', '')
        try:
            from smart_processor import SmartProcessor
            _sp = SmartProcessor(
                llm_flash=self.llm_flash,
                llm_pro=self.llm_pro,
                persona_context=_persona,
            )
            routing = _sp.process_rccam(state, llm_flash=self.llm_flash, llm_pro=self.llm_pro)
        except ImportError:
            # 降级：原地创建 SmartProcessor
            _sp = SmartProcessor(llm_flash=self.llm_flash, llm_pro=self.llm_pro, persona_context=_persona)
            routing = _sp.process_rccam(state, llm_flash=self.llm_flash, llm_pro=self.llm_pro)
        except Exception as e:
            logger.warning(f"SmartProcessor 调用失败,降级到基础 recall: {e}")
            _results = self.recall(query, top_k=8)
            _summary = "\n".join([r.get('content', r.get('user_text', ''))[:300] for r in (_results or [])[:5]])
            routing = {
                "rewritten_query": query,
                "flash_summary": _summary,
                "hub_context": _summary,
                "generated_answer": "",
                "confidence": 0.3,
            }

        state.analysis['rewritten_query'] = routing.get("rewritten_query", query)
        state.analysis['flash_summary'] = routing.get("flash_summary", "")
        state.analysis['hub_context'] = routing.get("hub_context", "")
        state.generated_answer = routing.get("generated_answer", "")
        state.answer_confidence = routing.get("confidence", 0.0)
        state.action_success = True
        state.analysis['action_delegated'] = True

        return state

    def _memory_phase(self, state: 'PhaseState') -> 'PhaseState':
        """
        R-CCAM 阶段 5: Memory(记忆阶段) — 元认知增强

        将本轮交互结果持久化。检索深度由元认知调节器控制（MCMA 适配）。
        """
        query = state.user_input
        answer = state.generated_answer

        # 人格标签：注入到记忆 metadata，后续检索时可依此过滤
        _persona_tag = (state.analysis.get('persona_context', '') or '')[:300]

        # 元认知决定是否存储（direct_answer 且熟悉 ≥ 0.9 跳过持久化，增强操作仍执行）
        skip_store = False
        if state.meta_strategy == "direct_answer" and state.meta_familiarity >= 0.9:
            skip_store = True
            logger.debug(f"[元认知] 跳过永久存储（直答+熟悉≥0.9），增强操作继续")
        # 即使 skip_store=True，DAG/情感/突触等增强操作仍执行

        # 1. 存储用户输入
        try:
            mem_id_input = self.remember(
                content=f"用户: {query}",
                metadata={
                    "type": "user_input", "cycle": state.cycle_count,
                    "knowledge_type": state.knowledge_type, "strategy": state.strategy,
                    "meta_strategy": state.meta_strategy, "confidence": state.answer_confidence,
                    "persona": _persona_tag,
                },
                source="user"
            )
            state.memory_ids.append(mem_id_input)
        except Exception as e:
            logger.warning(f"Memory phase - store user input failed: {e}")

        # 2. 存储系统回复
        if answer and not skip_store:
            try:
                mem_id_answer = self.remember(
                    content=f"系统: {answer}",
                    metadata={
                        "type": "system_reply", "cycle": state.cycle_count,
                        "strategy": state.strategy, "meta_strategy": state.meta_strategy,
                        "success": state.action_success,
                        "persona": _persona_tag,
                    },
                    source="ai"
                )
                state.memory_ids.append(mem_id_answer)
            except Exception as e:
                logger.warning(f"Memory phase - store answer failed: {e}")

        # 3. 写入 DAG（即使 skip_store=True 也执行，保证上下文连续性）
        if self.dag:
            try:
                # Galaxy 三维绑定: 注入 semantic_map / function_map / design_ref
                _galaxy_meta = {
                    'semantic_map': f"用户问题: {state.knowledge_type} / {state.intent}",
                    'function_map': f"_retrieval_phase → _cognition_phase → _control_phase → _action_phase",
                    'design_ref': f"xiaoyi_claw_api.py#L1614-L2604",
                    'meta_strategy': state.meta_strategy,
                    'knowledge_type': state.knowledge_type,
                    'intent': state.intent,
                    'complexity': state.meta_complexity,
                    'cycle': state.cycle_count,
                }
                self.dag.add_message(
                    session_key=state.session_key, role="user", content=query,
                    tokens=len(query) // 2,
                    importance=self._calculate_importance(query, state),
                    metadata=_galaxy_meta,
                )
                if answer:
                    _galaxy_meta_a = dict(_galaxy_meta)
                    _galaxy_meta_a['semantic_map'] = f"回答: {state.meta_strategy} / {state.strategy}, 置信度{state.answer_confidence:.2f}"
                    self.dag.add_message(
                        session_key=state.session_key, role="assistant", content=answer,
                        tokens=len(answer) // 2,
                        importance=self._calculate_importance(answer, state),
                        metadata=_galaxy_meta_a,
                    )
                state.dag_nodes_created = 2 if answer else 1
            except Exception as e:
                logger.warning(f"Memory phase - DAG write failed: {e}")

        # 4. 情感标记（增强操作，不依赖 skip_store）
        if hasattr(self, 'emotion_memory') and self.emotion_memory:
            try:
                emotion_result = self.emotion_memory.process_message(
                    user_message=query,
                    memory_content="系统回复: " + (answer[:200] if answer else "[无回复]")
                )
                state.emotion_marked = True
                state.analysis['emotion_result'] = {
                    "emotion": emotion_result.get("emotion", {}),
                    "weight": emotion_result.get("weight", 0.5),
                    "priority": emotion_result.get("priority", "normal"),
                }
            except Exception as e:
                logger.debug(f"Memory phase - emotion memory: {e}")

        # 5. 突触网络增强：创建神经元+突触连接（如果可用）
        if hasattr(self, 'synapse_network') and self.synapse_network:
            try:
                # 用户输入转为神经元
                user_neuron = self.synapse_network.create_neuron(
                    content=query[:500],
                )
                state.memory_ids.append(f"synapse:{user_neuron.id[:12]}")
                if answer:
                    answer_neuron = self.synapse_network.create_neuron(
                        content=answer[:800],
                    )
                    state.memory_ids.append(f"synapse:{answer_neuron.id[:12]}")
                    # 创建突触连接
                    self.synapse_network.create_synapse(
                        source_id=user_neuron.id,
                        target_id=answer_neuron.id,
                        weight=0.7,
                    )
                state.synapse_updated = True
            except Exception as e:
                logger.debug(f"Memory phase - synapse network: {e}")

        # 6. 自适应记忆优化周期（每 5 轮触发一次，轻量）
        if hasattr(self, 'adaptive_memory') and self.adaptive_memory:
            try:
                if state.cycle_count % 5 == 0:
                    opt_result = self.adaptive_memory.run_optimization_cycle()
                    if opt_result.get("params_changed"):
                        logger.info(f"AdaptiveMemory 参数优化: "
                            f"{len(opt_result.get('optimization', {}).get('adjustments', []))} 项调整")
            except Exception as e:
                logger.debug(f"Memory phase - adaptive memory: {e}")

        # 7. 自进化检查
        if state.cycle_count > 0 and state.action_success:
            state.evolution_triggered = True

        # 8. 内在元认知：每 10 轮用用户画像 + 思考技能分析体验数据
        #    不动其他模块，只写 state.analysis，供 Worker 后台消费
        if state.cycle_count > 0 and state.cycle_count % 10 == 0:
            if self.llm_flash:
                try:
                    from thinking_enhanced import ThinkingEnhanced
                    _te = ThinkingEnhanced(self.llm_flash)
                    _result = _te.introspect(max_samples=30)
                    if _result.get("success"):
                        state.analysis['self_evolution'] = _result
                        state.evolution_triggered = True
                        logger.info(f"[内在元认知] {len(_result.get('patterns',[]))} 条经验模式")
                except Exception as _ie:
                    logger.debug(f"[内在元认知] 分析跳过: {_ie}")

        # 9. Cognition Forest meta 子树：本轮元认知分析结果持久化到 DAG
        #    供下轮 assemble 或 Kernel 后台分析回溯
        if self.dag:
            try:
                _cog_payload = {
                    'session_key': state.session_key,
                    'cycle': state.cycle_count,
                    'knowledge_type': state.knowledge_type,
                    'intent': state.intent,
                    'meta_strategy': state.meta_strategy,
                    'strategy': state.strategy,
                    'complexity': state.meta_complexity,
                    'familiarity': state.meta_familiarity,
                    'retrieval_confidence': getattr(state, 'retrieval_confidence', 0),
                    'answer_confidence': state.answer_confidence,
                    'action_success': state.action_success,
                    'needs_more_info': getattr(state, 'needs_more_info', False),
                    'need_refine': getattr(state, 'meta_need_refine', False),
                    'has_emotion': state.analysis.get('emotion_result', {}).get('weight', 0) > 0.5 if state.analysis.get('emotion_result') else False,
                    'has_synapse': state.synapse_updated if hasattr(state, 'synapse_updated') else False,
                    'has_evolution': state.evolution_triggered if hasattr(state, 'evolution_triggered') else False,
                    'ts': time.time(),
                }
                self.dag.add_cognition_subtree(
                    "meta",
                    content=json.dumps(_cog_payload, ensure_ascii=False, default=str),
                    source="cognition_payload",
                )
            except Exception as e:
                logger.debug(f"Cognition Forest meta subtree: {e}")

        return state

    def _activate_callback(self, pattern: dict):
        """根据进化洞察激活下游模块（惰性加载）"""
        suggestion = (pattern.get("suggestion", "") or "").lower()
        if not suggestion:
            return
        try:
            # 参数调优建议
            if "参数" in suggestion or "调优" in suggestion or "调整" in suggestion:
                if not hasattr(self, '_auto_tuner') or self._auto_tuner is None:
                    from auto_tuner import AutoTuner
                    self._auto_tuner = AutoTuner()
                    logger.info("[内在元认知] 加载 AutoTuner")

            # 人格更新建议
            if "人格" in suggestion or "persona" in suggestion or "风格" in suggestion:
                if not hasattr(self, '_persona_updater') or self._persona_updater is None:
                    from auto_update_persona import PersonaAutoUpdater
                    self._persona_updater = PersonaAutoUpdater()
                    logger.info("[内在元认知] 加载 PersonaAutoUpdater")

            # 记忆优化建议
            if "记忆" in suggestion or "知识" in suggestion or "优化" in suggestion:
                if not hasattr(self, '_knowledge_refiner') or self._knowledge_refiner is None:
                    from knowledge_refiner import KnowledgeRefiner
                    self._knowledge_refiner = KnowledgeRefiner()
                    logger.info("[内在元认知] 加载 KnowledgeRefiner")

        except Exception:
            pass

    # ========================================================================
    # R-CCAM DAG 节点写入
    # ========================================================================

    def _write_rccam_node(self, state: 'PhaseState', phase_name, content,
                           strategy="", confidence=0.5, validation="unknown",
                           importance=0.5):
        """在 R-CCAM 每阶段末尾向 DAG 写入阶段节点"""
        if not self.dag:
            return
        try:
            session_key = getattr(state, 'session_key', 'xiaoyi-claw-dag')
            cycle_id = f"cycle_{session_key}_{state.cycle_count}"
            self.dag.add_rccam_node(
                session_key=session_key,
                cycle_id=cycle_id,
                cycle_index=state.cycle_count,
                phase_name=phase_name,
                content=content,
                strategy=strategy or "unknown",
                confidence=confidence,
                validation=validation,
                importance=importance,
                previous_cycle_id=getattr(state, 'previous_cycle_id', ''),
                metadata={"retrieval_confidence": getattr(state, 'retrieval_confidence', 0),
                          "answer_confidence": getattr(state, 'answer_confidence', 0),
                          "action_success": getattr(state, 'action_success', False),
                          "strategy": getattr(state, 'strategy', '')},
            )
        except Exception as e:
            logger.debug(f"_write_rccam_node ({phase_name}) failed: {e}")

        # 全透明事件：每写一个 rccam_node 就 PUB
        _cb = getattr(self, '_pub_event', None)
        if _cb:
            try:
                _cb('rccam:phase.end', {
                    'phase': phase_name,
                    'cycle': getattr(state, 'cycle_count', 1),
                    'session_key': session_key,
                    'cycle_id': cycle_id,
                    'strategy': strategy or '',
                    'confidence': confidence,
                    'validation': validation,
                    'content_len': len(str(content)) if content else 0,
                    'tokens': getattr(state, 'dag_nodes_created', 0),
                })
            except Exception:
                pass

    def _write_cycle_summary(self, state: 'PhaseState'):
        """写入整轮 cycle 的摘要节点"""
        if not self.dag:
            return

        # 全透明事件：cycle 完成
        _cb = getattr(self, '_pub_event', None)
        if _cb:
            try:
                _cb('rccam:cycle.end', {
                    'cycle': getattr(state, 'cycle_count', 1),
                    'session_key': getattr(state, 'session_key', ''),
                    'answer_len': len(getattr(state, 'generated_answer', '') or ''),
                    'confidence': getattr(state, 'answer_confidence', 0),
                    'strategy': getattr(state, 'strategy', ''),
                    'stop_reason': getattr(state, 'stop_reason', ''),
                    'memory_count': len(getattr(state, 'memory_ids', [])),
                })
            except Exception:
                pass

        try:
            session_key = getattr(state, 'session_key', 'xiaoyi-claw-dag')
            cycle_id = f"cycle_{session_key}_{state.cycle_count}"
            user_intent = f"{state.user_input[:100]} → {state.knowledge_type}"
            key_findings = [state.generated_answer[:150]] if state.generated_answer else []
            conclusion = state.generated_answer[:200] if state.generated_answer else ""
            source_phases = {
                "strategy": state.strategy if hasattr(state, 'strategy') else "",
                "type": state.knowledge_type if hasattr(state, 'knowledge_type') else "",
            }
            self.dag.write_cycle_summary(
                session_key=session_key,
                cycle_id=cycle_id,
                cycle_index=state.cycle_count,
                user_intent=user_intent,
                key_findings=key_findings,
                conclusion=conclusion,
                confidence=state.answer_confidence if hasattr(state, 'answer_confidence') else 0.5,
                source_phases=source_phases,
            )
        except Exception as e:
            logger.debug(f"_write_cycle_summary failed: {e}")

    def _calculate_importance(self, content: str, state: 'PhaseState') -> float:
        """计算内容的重要性分数"""
        high_importance = ["决定", "配置", "关键", "重要", "记住"]
        return 0.8 if any(kw in content for kw in high_importance) else 0.4

    def process(self, 
                user_input: str, 
                max_cycles: int = 1,
                store_memory: bool = True,
                has_image: bool = False,
                image_source: Optional[str] = None,
                session_key: str = "") -> Dict[str, Any]:
        """
        R-CCAM 结构化认知循环 - 统一入口

        对用户输入执行完整五阶段循环:
        Retrieval → Cognition → Control → Action → Memory

        Args:
            user_input: 用户输入文本
            max_cycles: 最大循环轮次(默认1,最多3)
            store_memory: 是否持久化记忆(默认是)
            has_image: 是否包含图片(Visual RAG 自动触发 OCR2)
            image_source: 图片源(URL/路径/二进制)

        Returns:
            处理结果,包含回答和状态信息
        """
        state = self.PhaseState(user_input)
        state.max_cycles = min(max_cycles, 3)
        # ═══ 延迟预算：每阶段时间熔断 ═══
        import time as _rt
        _process_start = _rt.time()
        _process_budget = 25.0  # 最大 25 秒
        def _budget_remaining(_ps=_process_start, _pb=_process_budget, _rt=_rt):
            return max(0.0, _pb - (_rt.time() - _ps))
        def _check_deadline(label='', _ps=_process_start, _pb=_process_budget, _rt=_rt):
            _rem = max(0.0, _pb - (_rt.time() - _ps))
            if _rem < 3.0:
                import logging as _lg
                _lg.getLogger(__name__).warning(f"R-CCAM budget exceeded ({label})")
                return True
            return False
        state.analysis['time_budget'] = 25.0

        state.has_image = has_image
        state.image_source = image_source

        # ── 独立 Reflexion:不依赖 thinking_enhanced ──
        # 从独立 JSON 文件读取反思记录，路由前先检查
        _reflexions_db = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'reflexions.json'
        )
        _reflexion_hits = []
        try:
            if os.path.exists(_reflexions_db):
                with open(_reflexions_db, 'r') as _f:
                    _all_refs = json.load(_f)
                # 取最近的 20 条做关键词匹配
                for _ref in (_all_refs[-20:] if len(_all_refs) > 20 else _all_refs):
                    _rq = _ref.get('question', '').lower()
                    _ru = user_input.lower()
                    # 如果当前 query 包含反思问题的关键词，或者反思问题包含当前 query 的关键词
                    _r_tokens = {t for t in _rq.split() if len(t) >= 2}
                    _u_tokens = {t for t in _ru.split() if len(t) >= 2}
                    _inter = _r_tokens & _u_tokens
                    if len(_inter) >= 2 or (len(_ru) > 3 and _rq in _ru) or (len(_rq) > 3 and _ru in _rq):
                        _reflexion_hits.append(_ref)
            if _reflexion_hits:
                _ref_ctx = "\n".join(
                    [f"[经验] {r.get('answer','')[:300]}" for r in _reflexion_hits[:3]]
                )
                state.analysis['reflexion_context'] = _ref_ctx
        except Exception:
            pass

        # 同时保留旧的 thinking_enhanced reflexion（如可用）
        try:
            if hasattr(self, 'thinking_enhanced') and self.thinking_enhanced:
                _te_refs = self.thinking_enhanced.reflexion.retrieve(user_input, top_k=2)
                if _te_refs:
                    _te_ctx = self.thinking_enhanced.reflexion.format_context(_te_refs)
                    if _te_ctx:
                        state.analysis['reflexion_context'] = _te_ctx
        except Exception:
            pass

        # ── Adaptive-RAG 预分类器：明显复杂的 query 直接 depth 3 ──
        _m_raw = user_input.strip()
        _complex_kws = ['分析','对比','比较','设计','架构','方案','原理','实现',
                       '区别','优缺点','方案','流程','步骤','怎么实现','如何实现',
                       '详细','完整','全面','评估','总结','归纳','研究','论述']
        _adaptive_rag_cplx = (
            len(_m_raw) >= 10  # 长 query 大概率复杂
            and ('?' in _m_raw or '？' in _m_raw  # 含问号
                 or any(kw in _m_raw for kw in _complex_kws)  # 含复杂关键词
                 or _m_raw.count(' ') >= 3)  # 超过 3 个空格（多实体）
        )
        state.analysis['adaptive_rag_cplx'] = _adaptive_rag_cplx  # 供路由使用

        # ── MultiPath:高复杂度问题先并行探索 ──
        try:
            if hasattr(self, 'thinking_enhanced') and self.thinking_enhanced and self.llm_flash:
                # 用 Flash 快速判断复杂度
                _complexity_check = self.llm_flash.chat.completions.create(
                    model=self._llm_flash_model,
                    messages=[{"role": "user", "content":
                        f"判断该问题的复杂度(返回 low/medium/high):{user_input[:200]}"}],
                    max_tokens=10, temperature=0.1,
                )
                _cplx = _complexity_check.choices[0].message.content.strip().lower()
                if _cplx == "high":
                    _mp_result = self.thinking_enhanced.multipath.explore(user_input)
                    if _mp_result.get("paths"):
                        state.analysis['multipath_context'] = \
                            _mp_result.get("best_path_reason", "") + "\n" + \
                            "\n".join([f"[路径] {p['perspective']}: {p['reasoning'][:100]}"
                                      for p in _mp_result["paths"] if p.get("reasoning")])
        except Exception:
            pass

        # ── Galaxy KoRa: 行为模式记录 ──
        self._kora_record_request(user_input=user_input, intent=state.intent or "",
                                   complexity=state.knowledge_type or "general")

        # ── Galaxy Cognition Forest: 子树注入（user/meta/self/env） ──
        if self.dag:
            try:
                _dag = self.dag
                # user 子树：当前用户输入快照
                _dag.add_cognition_subtree(
                    "user",
                    content=f"用户输入: {user_input[:300]}\n意图: {state.intent or '未知'}\n知识类型: {state.knowledge_type or '未知'}",
                    source="user_input",
                )
                # meta 子树：Reflexion 和 MultiPath 上下文
                _meta_parts = []
                if state.analysis.get('reflexion_context'):
                    _meta_parts.append(f"Reflexion: {state.analysis['reflexion_context'][:200]}")
                if state.analysis.get('multipath_context'):
                    _meta_parts.append(f"MultiPath: {state.analysis['multipath_context'][:200]}")
                if _meta_parts:
                    _dag.add_cognition_subtree(
                        "meta",
                        content="\n".join(_meta_parts),
                        source="process_meta",
                    )
            except Exception as e:
                logger.debug(f"Cognition Forest 子树注入失败: {e}")

        # ── DAG 上下文注入 ──
        if self.dag:
            try:
                # 优先用 session_key 精确读取
                _key = session_key if session_key else "xiaoyi-claw-dag"
                _ctx, _stats = self.dag.assemble_context(
                    session_key=_key,
                    fresh_tail_count=20,
                )
                if _ctx and len(_ctx.strip()) > 20:
                    state.analysis['current_dag_context'] = _ctx[:3000]
                    state.dag_nodes_created = _stats.get('node_count', 0)
            except Exception:
                pass

        # ── 人格注入：从 DAG 恢复人格节点（CRITICAL 优先，永不压缩）──
        #     供 R-CCAM 内部调用 LLM 时携带一致的人格定义
        if self.dag:
            try:
                _key = session_key if session_key else "xiaoyi-claw-dag"
                _persona_nodes = self.dag.get_session_nodes(
                    session_key=_key, node_type="persona", limit=1
                )
                if _persona_nodes:
                    _pn = _persona_nodes[0]
                    state.analysis['persona_context'] = _pn.content[:2000]
                    # 人格视觉：检测 DAG 节点 vs 文件状态
                    _dag_ts = _pn.timestamp
                    _latest_mtime = 0.0
                    for _fname in ["IDENTITY.md", "SOUL.md", "AGENTS.md"]:
                        _fp = os.path.join(WORKSPACE, _fname) if WORKSPACE else ""
                        if _fp and os.path.exists(_fp):
                            _mt = os.path.getmtime(_fp)
                            if _mt > _latest_mtime:
                                _latest_mtime = _mt
                    _needs_refresh = _latest_mtime > _dag_ts
                    _persona_meta = _pn.metadata or {}
                    state.analysis['persona_visual'] = {
                        "exists": True,
                        "dag_timestamp": _dag_ts,
                        "dag_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_dag_ts)),
                        "source": _persona_meta.get("source", "unknown"),
                        "file_latest_mtime": _latest_mtime,
                        "needs_refresh": _needs_refresh,
                        "chars": len(_pn.content or ""),
                    }
                else:
                    # DAG 没有人格节点 → 读文件兜底，标记过期
                    _persona_parts = []
                    for _fname in ["IDENTITY.md", "SOUL.md", "AGENTS.md"]:
                        _fp = os.path.join(WORKSPACE, _fname) if WORKSPACE else ""
                        if _fp and os.path.exists(_fp):
                            with open(_fp, "r", encoding="utf-8") as _f:
                                _persona_parts.append(_f.read(1500))
                    if _persona_parts:
                        state.analysis['persona_context'] = "\n\n".join(_persona_parts)[:2000]
                    state.analysis['persona_visual'] = {
                        "exists": False,
                        "source": "file_fallback",
                        "needs_refresh": True,
                        "note": "DAG 无人格节点，已从文件兜底恢复",
                    }
            except Exception:
                state.analysis['persona_visual'] = {
                    "exists": False,
                    "source": "error",
                    "needs_refresh": True,
                    "note": "人格注入异常",
                }

        # ── cognition_payload: 检索数据容器（供Hook注入会话模型） ──
        _cog_payload = {
            "memories": [],
            "dag": [],
            "kg_entities": [],
            "reflexion": "",
            "intent": "unknown",
            "routing": "",
            "user_input": user_input[:500],
            "rewritten_query": "",
            "reranked_results": [],
            "flash_summary": "",
            "skill_guide": "",
            "hub_context": "",
            "search_results": "",
            "weather_data": "",
            "user_location": "",
            "persona_visual": {},
            "persona_context": "",
        }
        def _update_cog(_s, _ud, _dbg):
            _cog_payload["memories"] = [m.get("content","")[:500] for m in (getattr(_s,"retrieved_memories",[]) or [])[:5]]
            _cog_payload["dag"] = [s.get("content","")[:500] for s in (getattr(_s,"dag_summaries",[]) or [])[:3]]
            _cog_payload["kg_entities"] = [e.get("name","") for e in (getattr(_s,"kg_entities",[]) or [])[:5]]
            _cog_payload["reflexion"] = (getattr(_s,"analysis",{}) or {}).get("reflexion_context","")[:500]
            _cog_payload["intent"] = getattr(_s,"intent","unknown")
            _cog_payload["routing"] = _dbg
            _cog_payload["skill_guide"] = ((getattr(_s,"analysis",{}) or {}).get("skill_guide","") or "")[:500]
            _ana = getattr(_s,"analysis",{}) or {}
            _cog_payload["rewritten_query"] = _ana.get("rewritten_query","")[:500]
            _cog_payload["reranked_results"] = _ana.get("reranked_results",[])[:8]
            _cog_payload["flash_summary"] = _ana.get("flash_summary","")[:2000]
            _cog_payload["hub_context"] = _ana.get("hub_context","")[:3000]
            _cog_payload["search_results"] = _ana.get("search_results","")[:3000]
            _cog_payload["weather_data"] = _ana.get("weather_data","")[:300]
            _cog_payload["user_location"] = _ana.get("user_location","")[:200]
            _cog_payload["merged_context"] = _ana.get("merged_context","")[:3000]
            _cog_payload["merged_count"] = _ana.get("merged_count", 0)
            _cog_payload["merged_sources"] = _ana.get("merged_sources", [])
            _cog_payload["persona_visual"] = _ana.get("persona_visual", {})
            _cog_payload["persona_context"] = _ana.get("persona_context", "")[:500]

        # ── 重放缓冲区:从 verified_memories 采样预热 ──
        try:
            _vm_path = os.path.join(WORKSPACE, ".learnings", "verified_memories.jsonl") if WORKSPACE else ""
            if _vm_path and os.path.exists(_vm_path):
                _vm_entries = []
                with open(_vm_path, "r") as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if _line:
                            try:
                                _vm_entries.append(json.loads(_line))
                            except Exception:
                                pass

                # 相关度排序:按 content 关键字匹配
                _query_keywords = set(user_input.lower().split())
                _scored = []
                for _entry in _vm_entries:
                    _conf = _entry.get("confidence", 0)
                    if _conf < 0.7:
                        continue
                    _content = _entry.get("content", "").lower()
                    _match_count = sum(1 for kw in _query_keywords if kw in _content)
                    _scored.append((_match_count * 0.2 + _conf * 0.8, _entry))

                _scored.sort(key=lambda x: x[0], reverse=True)
                _top_replay = [e for s, e in _scored[:3] if s > 0.3]

                if _top_replay:
                    _replay_text = "\n".join([f"[参考记忆] {e.get('content', '')[:200]}" for e in _top_replay])
                    state.analysis['replay_buffer'] = _replay_text
        except Exception:
            pass

        # ═══ 多意图检测 + 子问题拆分 (PruneRAG + CompactRAG 入口) ═══
        _m = user_input.strip()
        _state_sub_queries = []
        _multi_intent_detected = False
        if len(_m) >= 12 and (_m.count('和') + _m.count('与') + _m.count('、') + _m.count(' vs ') + _m.count(' VS ') + _m.count('区别') + _m.count('对比') + _m.count('比较') + _m.count('差异')) >= 1:
            # 用连接词做粗切分
            _split_parts = [p.strip() for p in re.split(r'[和与及以及、,，]', _m) if len(p.strip()) >= 4]
            # 过滤助词/停用词
            _stop_terms = {'的','了','吗','么','呢','吧','啊','呀','哦','嗯','哈','哈','不'}
            _significant = []
            for p in _split_parts:
                # 中英文混合场景：用字符数判断，不用 token 数
                _clean_chars = ''.join(c for c in p if c not in _stop_terms and c != ' ')
                if len(_clean_chars) >= 4:
                    _significant.append(p)
            if len(_significant) >= 2:
                _multi_intent_detected = True
                _state_sub_queries = _significant[:4]  # 最多 4 个子问题
                state.analysis['multi_intent'] = True
                state.analysis['sub_queries'] = _state_sub_queries

        # ═══ 负反馈学习：用户纠正→写 reflexion ═══
        _neg_markers = ['不对','不是','错了','不对吧','不是这个','不是这样','不对的',
                       '错','错了','不对的','不是那','你说错了','你说得不对',
                       '不是我想','不对不对','不是这个意思','不对，','不是，','不是我要']
        _user_corrected = False
        for _nm in _neg_markers:
            if _nm in _m:
                _user_corrected = True
                break
        if _user_corrected:
            state.analysis['user_correction_detected'] = True
            try:
                _ref_file = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'reflexions.json'
                )
                _ref_dir = os.path.dirname(_ref_file)
                if not os.path.exists(_ref_dir):
                    os.makedirs(_ref_dir, exist_ok=True)
                _all_refs = []
                if os.path.exists(_ref_file):
                    with open(_ref_file, 'r') as _f:
                        try: _all_refs = json.load(_f)
                        except: _all_refs = []
                _ref_entry = {
                    'question': _m[:200],
                    'answer': '[用户纠正] 之前的回答不正确',
                    'scores': {'faithfulness': 1, 'relevance': 1, 'completeness': 1},
                    'timestamp': datetime.utcnow().isoformat(),
                    'type': 'user_correction',
                    'priority': 'high',
                }
                _all_refs.append(_ref_entry)
                with open(_ref_file, 'w') as _f:
                    json.dump(_all_refs[-50:], _f, ensure_ascii=False, indent=2)
            except Exception:
                pass


        # ═══ 自适应路由：渐进式深度决策 ═══
        # 路由信号全部来自执行过程自然产出，不使用外部分类器
        _q = '?' in _m or '？' in _m
        _dag_ctx = state.analysis.get('current_dag_context', '')
        _route_depth = 0  # 0=直答, 1=DAG直答, 2=轻量检索, 3=完整管线
        _routing_signals = {"has_dag": bool(_dag_ctx and len(_dag_ctx.strip()) > 50),
                            "multi_intent": _multi_intent_detected}

        # ── 中文问句特征检测（无问号也算问题） ──
        _question_markers = ['?','？','吗','么','呢','吧','吗?','吗？','什么','怎么','为啥','为何',
                            '如何','哪','谁','啥','何','岂','难道','何必','是否','有没']
        _has_q = _q or any(m in _m for m in _question_markers)

        # ── 阈值自适应：从 routing_stats 读取动态阈值 ──
        _adaptive_threshold = 0.55
        _routing_stats_path = state.analysis.get('_routing_stats_path', '')
        if not _routing_stats_path:
            _routing_stats_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'routing_stats.json')
            state.analysis['_routing_stats_path'] = _routing_stats_path
        try:
            if os.path.exists(_routing_stats_path):
                with open(_routing_stats_path, 'r') as _f:
                    _rs = json.load(_f)
                # 统计深度 1 的直答后是否被追问（即后续相同主题走了 depth 2+）
                _d1_total = _rs.get('depth1_total', 0)
                _d1_fallbacks = _rs.get('depth1_fallbacks', 0)
                if _d1_total >= 5:
                    _fallback_rate = _d1_fallbacks / max(_d1_total, 1)
                    # fallback 率 > 30% → 提高阈值（更保守，少走 DAG 直答）
                    # fallback 率 < 5% → 降低阈值（更激进，多走 DAG 直答）
                    if _fallback_rate > 0.30:
                        _adaptive_threshold = min(0.85, 0.55 + (_fallback_rate - 0.30))
                    elif _fallback_rate < 0.05 and _d1_total > 10:
                        _adaptive_threshold = max(0.30, 0.55 - 0.10)
        except Exception:
            pass
        _routing_signals['adaptive_threshold'] = round(_adaptive_threshold, 2)

        # 所有请求走统一完整管线（深度0/1/2已移除,都由主模型回答）
        _route_depth = 3
        _routing_signals['reason'] = 'unified_pipeline'

        state.analysis['routing'] = {"depth": _route_depth, "signals": _routing_signals}
        _routing_debug = f"d{_route_depth}|tk={_routing_signals.get('dag_token_match','?')}|at={_adaptive_threshold}|{_routing_signals.get('reason','')}|"

        # ═══ 多意图分解：子问题独立检索 + CompactRAG 合并 ═══
        _multi_intent = state.analysis.get('multi_intent', False)
        _sub_queries = state.analysis.get('sub_queries', [])
        _compact_rag_done = False
        if _multi_intent and len(_sub_queries) >= 2:
            try:
                # 1) 每个子问题走独立检索
                _sub_results = []
                for _sq in _sub_queries:
                    _sr = []
                    if self.dag:
                        _sr, _ = self.dag.retrieve(_sq, top_k=3)
                    _vm = self.retrieve_memories(_sq, top_k=2)
                    if isinstance(_vm, list):
                        for _v in _vm:
                            _c = _v.get('content','')[:300] if isinstance(_v, dict) else str(_v)[:300]
                            if _c:
                                _sr.append({'content': _c, 'source': 'memory'})
                    _sub_results.append({'query': _sq, 'results': _sr[:5]})

                # 2) CompactRAG: 合并 prompt 一次性生成
                _sub_ctx_parts = []
                for _sr_info in _sub_results:
                    _sq = _sr_info['query']
                    _sr_texts = []
                    for _r in _sr_info['results'][:4]:
                        _c = _r.get('content','')[:200]
                        if _c:
                            _sr_texts.append('  - ' + _c)
                    if _sr_texts:
                        _sub_ctx_parts.append('[' + _sq[:60] + ']:\\n' + '\\n'.join(_sr_texts))

                if _sub_ctx_parts:
                    _merged_ctx = '\\n\\n'.join(_sub_ctx_parts)
                    _compact_prompt = (
                        '用户的问题包含多个方面,请分别回答每个子问题,最后总结。\\n\\n'
                        '原始问题: ' + user_input[:300] + '\\n\\n'
                        '子问题与参考资料:\\n' + _merged_ctx[:3500] + '\\n\\n'
                        '请按以下格式回答:\\n'
                        '1. [子问题1]: <简要回答>\\n'
                        '2. [子问题2]: <简要回答>\\n'
                        '...\\n'
                        '总结: <一句话总结>。\\n\\n回答:'
                    )
                    _cx_rsp = self.llm_flash.chat.completions.create(
                        model=self._llm_flash_model,
                        messages=[{'role': 'user', 'content': _compact_prompt}],
                        max_tokens=1024,
                        temperature=0.3,
                    )
                    _cx_ans = _cx_rsp.choices[0].message.content.strip()
                    if _cx_ans and len(_cx_ans) > 50:
                        state.generated_answer = _cx_ans
                        state.answer_confidence = 0.75
                        state.strategy = 'answer'
                        state.action_success = True
                        state.stop_reason = 'compact_rag_merged'
                        state.analysis['compact_rag'] = True
                        state.analysis['sub_results'] = _sub_results
                        state.analysis['compact_rag_count'] = len(_sub_queries)
                        _compact_rag_done = True

                        # 3) PruneRAG: 低置信度子问题补检索
                        _prune_sub_segments = re.split(r'\\d+\\.\\s*\\[', _cx_ans)
                        _prune_results = []
                        for _i, _seg in enumerate(_prune_sub_segments):
                            if not _seg.strip() or _i >= len(_sub_queries):
                                continue
                            _pq = _sub_queries[_i]
                            _is_low_confidence = (
                                len(_seg) < 20
                                or '抱歉' in _seg
                                or '暂未' in _seg
                                or '不知道' in _seg
                                or '无法' in _seg
                                or '没有' in _seg
                            )
                            if _is_low_confidence and len(_seg) < 100:
                                _prune_dag, _ = self.dag.retrieve(_pq, top_k=5) if self.dag else ([], {})
                                _prune_vm = self.retrieve_memories(_pq, top_k=3)
                                _prune_extra_parts = []
                                for _r in (_prune_dag or []) + (_prune_vm or []):
                                    _c = _r.get('content','')[:300] if isinstance(_r, dict) else str(_r)[:300]
                                    if _c:
                                        _prune_extra_parts.append(_c)
                                if _prune_extra_parts:
                                    _prune_results.append({'query': _pq, 'extra': _prune_extra_parts[:4]})
                        if _prune_results:
                            _prune_ctx_parts = []
                            for _pr in _prune_results:
                                _pq = _pr['query']
                                _pt = '\\n'.join(['  - ' + r for r in _pr['extra']])
                                _prune_ctx_parts.append('[' + _pq[:60] + '] 补充资料:\\n' + _pt)
                            _prune_prompt = (
                                '以下子问题的回答置信度较低,请根据补充资料修正。\\n\\n'
                                '原始合并回答:\\n' + _cx_ans[:1500] + '\\n\\n'
                                '需要修正的子问题补充资料:\\n' + '\\n\\n'.join(_prune_ctx_parts)[:2500] + '\\n\\n'
                                '请输出修正后的完整合并版本(保留所有子问题回答,只修正低置信度的部分):'
                            )
                            _pr_rsp = self.llm_flash.chat.completions.create(
                                model=self._llm_flash_model,
                                messages=[{'role': 'user', 'content': _prune_prompt}],
                                max_tokens=1024,
                                temperature=0.2,
                            )
                            _pr_ans = _pr_rsp.choices[0].message.content.strip()
                            if _pr_ans and len(_pr_ans) > 50:
                                state.generated_answer = _pr_ans
                                state.answer_confidence = min(state.answer_confidence + 0.1, 0.9)
                                state.stop_reason = 'compact_rag_pruned'
                                state.analysis['compact_rag_pruned'] = len(_prune_results)
            except Exception:
                pass

        # ── 如果 CompactRAG 已完成,跳过主循环直接返回 ──
        if _compact_rag_done:
            _sub_total = sum(len(s['results']) for s in _sub_results) if _sub_results else 0
            return {
                'answer': state.generated_answer,
                'confidence': state.answer_confidence,
                'critic_context': None,
                'routing_debug': _routing_debug + '|multi=' + str(len(_sub_queries)),
                'strategy': 'answer',
                'knowledge_type': 'context_based',
                'intent': 'multi_intent',
                'cycle_count': 0,
                'thinking_skills_used': [],
                'retrieval_confidence': 0.75,
                'memory_ids': [],
                'stop_reason': state.stop_reason,
                'action_success': True,
                'phase_logs': state.phase_logs,
                'cognition_payload': _cog_payload,
                'rccam_phase_states': {
                    'retrieval': {'memories_count': _sub_total, 'multi_intent': True, 'compact_rag_count': len(_sub_queries), 'needs_more_info': False},
                    'cognition': {'type': 'multi_intent', 'type_confidence': 0.75, 'thinking_skills': []},
                    'control': {'strategy': 'answer', 'fallback': 'default', 'prune_rag': state.analysis.get('compact_rag_pruned', False)},
                    'action': {'success': True, 'error': None},
                    'memory': {'memory_count': 0, 'dag_nodes': 0, 'emotion_marked': False, 'compact_rag': True},
                },
            }

        # ── 元认知初始化为默认（Control 阶段会覆盖） ──
        _meta_budget = max(state.meta_reasoning_budget, 1)

        while state.cycle_count < min(state.max_cycles, _meta_budget + 1) and not state.should_stop:
            state.cycle_count += 1
            # 全透明事件：cycle 开始
            _cb = getattr(self, '_pub_event', None)
            if _cb:
                try:
                    _cb('rccam:cycle.start', {
                        'cycle': state.cycle_count,
                        'max_cycles': state.max_cycles,
                        'session_key': getattr(state, 'session_key', ''),
                    })
                except Exception:
                    pass

            # 设置跨 cycle 依赖链：上一轮的 cycle_id
            if state.cycle_count > 1:
                session_key = getattr(state, 'session_key', 'xiaoyi-claw-dag')
                state.previous_cycle_id = f"cycle_{session_key}_{state.cycle_count - 1}"

            # 元认知早期退出（direct_answer/light_retrieval 不走完整管线）
            if state.cycle_count > 1:
                if state.meta_strategy in ("direct_answer",):
                    state.should_stop = True
                    state.stop_reason = f"meta_direct_answer"
                    break
                if state.meta_strategy == "light_retrieval" and state.generated_answer and state.answer_confidence >= 0.3:
                    state.should_stop = True
                    state.stop_reason = "meta_light_retrieval_ok"
                    break
                if state.meta_reasoning_budget > 0 and state.cycle_count > state.meta_reasoning_budget:
                    state.should_stop = True
                    state.stop_reason = "meta_budget_exhausted"
                    break

            # 延迟熔断检查
            if _check_deadline('cycle_' + str(state.cycle_count)):
                if state.generated_answer:
                    state.should_stop = True
                    state.stop_reason = 'time_budget_exceeded'
                    break


            # ═══ 阶段日志采集 R-CCAM 直播 ═══
            _plt = lambda: _rccam_tm.time()  # 时间戳工厂
            _pl = state.phase_logs

            # 阶段 1: Retrieval
            _t0 = _plt()
            if state.cycle_count > 1 and state.dag_nodes_created > 0:
                prev_cycle_query = f"{state.user_input} (补充信息: 上一轮回答到 '{state.generated_answer[:300]}')"
                state = self._retrieval_phase(state, custom_query=prev_cycle_query)
            else:
                state = self._retrieval_phase(state)
            _pl.append({"cycle":state.cycle_count,"phase":"retrieval","detail":f"置信度={state.retrieval_confidence:.2f},记忆={len(state.retrieved_memories)}条,DAG={len(state.dag_summaries)}条","elapsed_ms":round((_plt()-_t0)*1000),"ts":_t0})
            self._write_rccam_node(state, "retrieval",
                content=f"置信度={state.retrieval_confidence:.2f}, 记忆{len(state.retrieved_memories)}条, DAG摘要{len(state.dag_summaries)}条, 策略={state.strategy}",
                strategy=state.strategy,
                importance=state.meta_knowledge_density)

            # 阶段 2: Cognition
            _t0 = _plt()
            state = self._cognition_phase(state)
            _ana = state.analysis or {}
            state.meta_complexity = float(_ana.get("complexity", 0.5))
            state.meta_familiarity = 1.0 - float(_ana.get("confusion", 0.5))
            _kd_type_map = {"decision":0.7,"error":0.7,"task":0.7,"info":0.8,"query":0.8,"architecture":0.8,"evaluation":0.75,"debug":0.7,"code":0.75,"procedure":0.65,"explanation":0.6,"review":0.7,"prototype":0.7,"problem_solving":0.75,"creative":0.6,"investigation":0.75,"conflict":0.7,"practice":0.65,"focus":0.6,"planning":0.7,"collect":0.65,"improve":0.7,"seed":0.6,"theory":0.8,"strategy":0.75,"automation":0.7,"general":0.5}
            state.meta_knowledge_density = _kd_type_map.get(state.knowledge_type, 0.5)
            _pl.append({"cycle":state.cycle_count,"phase":"cognition","detail":f"类型={state.knowledge_type},复杂度={_ana.get('complexity',0.5):.2f},困惑={_ana.get('confusion',0.5):.2f}","elapsed_ms":round((_plt()-_t0)*1000),"ts":_t0})
            self._write_rccam_node(state, "cognition",
                content={"type": state.knowledge_type, "complexity": state.meta_complexity,
                         "intent": state.intent, "thinking_skills": state.thinking_skills_used,
                         "reflexion": str(_ana.get("reflexion_context",""))[:200]},
                strategy=state.knowledge_type,
                importance=state.meta_knowledge_density)

            # 阶段 3: Control
            _t0 = _plt()
            state = self._control_phase(state)
            _pl.append({"cycle":state.cycle_count,"phase":"control","detail":f"策略={state.strategy},回退={state.fallback},元预算={_meta_budget}","elapsed_ms":round((_plt()-_t0)*1000),"ts":_t0})
            self._write_rccam_node(state, "control",
                content=f"策略={state.strategy}, 元策略={state.meta_strategy}, 回退={state.fallback}, 复杂度={state.meta_complexity:.2f}",
                strategy=state.strategy,
                importance=0.6)

            # 阶段 4: Action
            _t0 = _plt()
            state = self._action_phase(state)
            _pl.append({"cycle":state.cycle_count,"phase":"action","detail":f"成功={state.action_success},置信度={state.answer_confidence:.2f},回答长度={len(state.generated_answer)}","elapsed_ms":round((_plt()-_t0)*1000),"ts":_t0})
            self._write_rccam_node(state, "action",
                content=state.generated_answer,
                strategy=state.strategy,
                confidence=state.answer_confidence,
                validation="passed" if state.action_success else "failed",
                importance=0.8 if state.action_success else 0.3)

            # 阶段 5: Memory
            _t0 = _plt()
            state = self._memory_phase(state)
            _pl.append({"cycle":state.cycle_count,"phase":"memory","detail":f"记忆={len(state.memory_ids)}条,DAG节点={state.dag_nodes_created}个,突触={state.synapse_updated},情感={state.emotion_marked}","elapsed_ms":round((_plt()-_t0)*1000),"ts":_t0})
            self._write_rccam_node(state, "memory",
                content={"mem_ids": state.memory_ids, "dag_nodes": state.dag_nodes_created,
                         "synapse": state.synapse_updated, "emotion": state.emotion_marked},
                strategy="memory",
                importance=0.5)
            # 循环结束后写 cycle_summary
            self._write_cycle_summary(state)

            # ── 多Agent批评者:输出 context 数据,由 Worker 层 spawn 子进程调 Pro ──
            _critic_context = None
            if state.action_success and state.generated_answer and state.strategy == "answer":
                _replay_ctx = state.analysis.get('replay_buffer', '')
                _skill_ctx = state.analysis.get('skill_guide', '')
                _extra_ctx = ''
                if _replay_ctx:
                    _extra_ctx = f'\n参考知识:\n{_replay_ctx}'
                if _skill_ctx and '【验证记忆参考】' not in _skill_ctx:
                    _extra_ctx += f'\n方法指导:\n{_skill_ctx[:300]}'
                _critic_context = {
                    "user_input": state.user_input[:500],
                    "answer": state.generated_answer[:1500],
                    "extra_context": _extra_ctx,
                }

            # ── LLM-as-Judge 自评分 ────────────────────
            if state.action_success and state.generated_answer:
                try:
                    judge_prompt = f"""请对以下回答进行质量评分(返回JSON格式):

用户问题: {state.user_input[:300]}

AI回答: {state.generated_answer[:1000]}

请打分(1-10):
- "faithfulness": 忠实度(是否有幻觉或虚构)
- "relevance": 相关性(是否直接回答问题)
- "completeness": 完整性(信息是否充分)

返回格式: {{"faithfulness": 8, "relevance": 7, "completeness": 6}}"""

                    judge_rsp = self.llm_flash.chat.completions.create(
                        model=self._llm_flash_model,
                        messages=[{"role": "user", "content": judge_prompt}],
                        max_tokens=200, temperature=0.1,
                        extra_body={"user_id": "xiaoyi-claw-judge"},
                    )
                    judge_text = judge_rsp.choices[0].message.content.strip()
                    
                    # 解析评分(容忍非标准JSON)
                    import json as _json
                    scores = {}
                    try:
                        scores = _json.loads(judge_text)
                    except Exception:
                        # 尝试从文本中提取数字
                        import re as _re
                        _f = _re.search(r'faithfulness[\s:\"]+(\d+)\"?', judge_text)
                        _r = _re.search(r'relevance[\s:\"]+(\d+)\"?', judge_text)
                        _c = _re.search(r'completeness[\s:\"]+(\d+)\"?', judge_text)
                        scores['faithfulness'] = int(_f.group(1)) if _f else 5
                        scores['relevance'] = int(_r.group(1)) if _r else 5
                        scores['completeness'] = int(_c.group(1)) if _c else 5
                    faithfulness = scores.get("faithfulness", 5)
                    relevance = scores.get("relevance", 5)
                    completeness = scores.get("completeness", 5)
                    _avg_score = (faithfulness + relevance + completeness) / 3

                    # 全透明事件：judge 评分结果
                    _cb_j = getattr(self, '_pub_event', None)
                    if _cb_j:
                        try:
                            _cb_j('rccam:judge.result', {
                                'cycle': getattr(state, 'cycle_count', 1),
                                'session_key': getattr(state, 'session_key', ''),
                                'faithfulness': faithfulness,
                                'relevance': relevance,
                                'completeness': completeness,
                                'avg_score': round(_avg_score, 1),
                                'strategy': getattr(state, 'strategy', ''),
                            })
                        except Exception:
                            pass

                    # 高分(三项都≥7)→ 存 verified_memories
                    if faithfulness >= 7 and relevance >= 7 and completeness >= 7:
                        vm_path = os.path.join(WORKSPACE, ".learnings", "verified_memories.jsonl") if WORKSPACE else ""
                        if vm_path and os.path.exists(os.path.dirname(vm_path)):
                            vm_entry = {
                                "id": f"VM-AUTO-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
                                "content": f"Q: {state.user_input[:200]}\nA: {state.generated_answer[:500]}",
                                "source": "ai_judge",
                                "confidence": round((faithfulness + relevance + completeness) / 30, 2),
                                "created_at": datetime.utcnow().isoformat(),
                                "valid_from": datetime.utcnow().isoformat(),
                                "valid_until": "",
                                "verification_status": "verified",
                                "verified_at": datetime.utcnow().isoformat(),
                                "verified_by": "llm_judge",
                                "related_entities": [],
                                "evidence_ids": [],
                                "conflict_ids": [],
                                "tags": ["auto_judged", state.strategy],
                                "importance": 0.6,
                            }
                            with open(vm_path, "a") as f:
                                f.write(_json.dumps(vm_entry, ensure_ascii=False) + "\n")

                    # ── Reflexion:低分记录反思 ──
                    if _avg_score < 7 and hasattr(self, 'thinking_enhanced') and self.thinking_enhanced:
                        try:
                            self.thinking_enhanced.reflexion.record(
                                question=state.user_input,
                                answer=state.generated_answer,
                                scores={"faithfulness": faithfulness, "relevance": relevance, "completeness": completeness},
                                flash_client=self.llm_flash,
                            )
                        except Exception:
                            pass

                    # ── Self-Refine:低分自动精炼 ──
                    if _avg_score < 7 and hasattr(self, 'thinking_enhanced') and self.thinking_enhanced:
                        try:
                            _refined, _history = self.thinking_enhanced.refine.refine(
                                question=state.user_input,
                                initial_answer=state.generated_answer,
                                judge_func=lambda q, a: (scores, f"忠实度{faithfulness}/10、相关性{relevance}/10、完整性{completeness}/10。需要改进")
                            )
                            if _refined and _refined != state.generated_answer:
                                state.generated_answer = _refined
                                state.answer_confidence = max(state.answer_confidence, 0.6)
                        except Exception:
                            pass

                    # ── FLARE 式缓存：低分触发的同轮次检索重答 ──
                    # 如果自评分低(特别是忠实度<6)，表示当前回答证据不足
                    # 不走完整下一轮，而是快速检索后重答
                    if _avg_score < 7 and faithfulness < 7 and state.cycle_count <= 1:
                        try:
                            if self.dag:
                                _flare_dag, _ = self.dag.retrieve(state.user_input, top_k=4)
                            else:
                                _flare_dag = []

                            _flare_vm = self.retrieve_memories(state.user_input, top_k=3)
                            _flare_ctx_parts = []
                            for _r in (_flare_dag or []):
                                _c = _r.get('content', '')[:400]
                                if _c:
                                    _flare_ctx_parts.append(f"[对话历史] {_c}")
                            for _r in (_flare_vm or []):
                                _c = _r.get('content', '')[:400] if isinstance(_r, dict) else str(_r)[:400]
                                if _c:
                                    _flare_ctx_parts.append(f"[记忆] {_c}")

                            if _flare_ctx_parts:
                                _flare_ctx = "\n".join(_flare_ctx_parts[:6])
                                _flare_prompt = (
                                    '重新回答以下问题,基于提供的参考资料修正之前回答中的不足。\n\n'
                                    f'问题: {state.user_input[:300]}\n\n'
                                    f'参考资料:\n{_flare_ctx[:2500]}\n\n'
                                    '回答(完整、忠实于参考资料):'
                                )
                                _flare_rsp = self.llm_flash.chat.completions.create(
                                    model=self._llm_flash_model,
                                    messages=[{"role": "user", "content": _flare_prompt}],
                                    max_tokens=1024,
                                    temperature=0.2,
                                )
                                _flare_ans = _flare_rsp.choices[0].message.content.strip()
                                if _flare_ans and len(_flare_ans) > 20:
                                    state.generated_answer = _flare_ans
                                    state.answer_confidence = min(state.answer_confidence + 0.15, 0.9)
                                    state.analysis['flare_salvage'] = True
                        except Exception:
                            pass
                except Exception:
                    pass

            # 阶段 5: Memory(每轮不在这里持久化，由外部调用者回复后存储)
            # Memory 在外部调用者的回答之后执行，避免存储空的 generated_answer
            pass

            # ── 元认知满意化终止（MedCoG + Meta-R1 S3） ──
            if state.meta_early_stop:
                state.should_stop = True
                state.stop_reason = "meta_early_stop"
            elif state.meta_strategy == "deep_reasoning" and state.answer_confidence >= 0.7:
                state.should_stop = True
                state.stop_reason = "meta_satisfied"
                state.meta_need_refine = False
            elif state.meta_strategy == "direct_answer" and state.cycle_count >= 1:
                state.should_stop = True
                state.stop_reason = "meta_direct_answer"
            # 原有停止条件
            elif state.strategy == "answer" or state.strategy == "boundary_violation":
                state.should_stop = True
                state.stop_reason = "strategy_completed"
            elif state.action_success and state.answer_confidence >= 0.3:
                state.should_stop = True
                state.stop_reason = "confidence_met"
            elif state.cycle_count >= state.max_cycles:
                state.should_stop = True
                state.stop_reason = "max_cycles_reached"

        # ── 路由统计：记录本次路由决策供阈值自适应 ──
        if state.analysis.get('light_retrieval_failed'):
            # depth 1 直答后又被完整管线覆盖 → 记一次 depth 1 回退
            _try_record_routing_fallback(_routing_stats_path)

        # 生成 session_key（供外部 Memory 回调）
        _session_key = state.session_key if state.session_key else f"rccam_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # ═══ 消息级元认知趋势记录 ═══
        try:
            self._process_meta_trend(state)
        except Exception:
            pass

        # 返回结果（不含记忆阶段，由外部调用者在回复后调用 save_memory）
        _update_cog(state, user_input, _routing_debug)
        return {
            "answer": state.generated_answer,
            "confidence": state.answer_confidence,
            "phase_logs": state.phase_logs,
            "critic_context": _critic_context,
            "routing_debug": _routing_debug,
            "strategy": state.strategy,
            "meta_strategy": state.meta_strategy,
            "knowledge_type": state.knowledge_type,
            "intent": state.intent,
            "cycle_count": state.cycle_count,
            "session_key": _session_key,
            "thinking_skills_used": state.thinking_skills_used,
            "retrieval_confidence": state.retrieval_confidence,
            "memory_ids": state.memory_ids,
            "stop_reason": state.stop_reason,
            "action_success": state.action_success,
            "cognition_payload": _cog_payload,
            "rccam_phase_states": {
                "retrieval": {
                    "memories_count": len(state.retrieved_memories),
                    "dag_summaries_count": len(state.dag_summaries),
                    "kg_entities_count": len(state.kg_entities),
                    "needs_more_info": state.needs_more_info,
                    "cognition_payload": {
                "retrieved_memories": [m.get("content","")[:500] for m in (getattr(state,"retrieved_memories",[]) or [])[:5]],
                "dag_summaries": [s.get("content","")[:500] for s in (getattr(state,"dag_summaries",[]) or [])[:3]],
                "kg_entities": [e.get("name","") for e in (getattr(state,"kg_entities",[]) or [])[:5]],
                "reflexion_context": getattr(state,"analysis",{}).get("reflexion_context",""),
                "routing_debug": _routing_debug if "_routing_debug" in dir() else "",
                "intent": getattr(state,"intent","unknown"),
                "knowledge_type": getattr(state,"knowledge_type","unknown"),
                "has_dag": bool(getattr(state,"dag_summaries",[])),
                "has_memories": bool(getattr(state,"retrieved_memories",[])),
                "user_input": user_input[:500],
            },
        },
                "cognition": {
                    "type": state.knowledge_type,
                    "type_confidence": state.type_confidence,
                    "thinking_skills": state.thinking_skills_used,
                },
                "control": {
                    "strategy": state.strategy,
                    "fallback": state.fallback,
                },
                "action": {
                    "success": state.action_success,
                    "error": state.action_error,
                },
                "memory": {
                    "memory_count": len(state.memory_ids),
                    "dag_nodes": state.dag_nodes_created,
                    "emotion_marked": state.emotion_marked,
                }
            }
        }

    def save_memory(self, session_key: str, user_input: str, answer: str,
                     metadata: Optional[Dict] = None) -> bool:
        """
        外部记忆保存（回答后调用）

        在 R-CCAM 管线生成分析结果后，由外部调用者（AI 回复后）
        传入真正的 answer 来持久化记忆。

        Args:
            session_key: process() 返回的 session_key
            user_input: 用户的原问题
            answer: AI 的真实回答内容
            metadata: 额外的元数据（可选）

        Returns:
            是否成功
        """
        if not session_key or not answer:
            logger.warning("[Memory save_memory] 跳过：session_key 或 answer 为空")
            return False

        # 构造一个临时 PhaseState 供 _memory_phase 调用
        _state = self.PhaseState(user_input)
        _state.session_key = session_key
        _state.generated_answer = answer
        _state.meta_strategy = (metadata or {}).get("meta_strategy", "n/a")
        _state.strategy = (metadata or {}).get("strategy", "n/a")
        _state.knowledge_type = (metadata or {}).get("knowledge_type", "n/a")
        _state.answer_confidence = (metadata or {}).get("confidence", 0.7)
        _state.cycle_count = (metadata or {}).get("cycle_count", 1)

        # 执行记忆阶段
        try:
            _state = self._memory_phase(_state)
            logger.info(f"[Memory save_memory] 已持久化（session_key={session_key}）")
            return True
        except Exception as e:
            logger.error(f"[Memory save_memory] 失败: {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """
        获取系统状态

        Returns:
            状态信息
        """
        status = {
            'vector_store': None,
            'ontology': None,
            'brain_sync': None,
            'multimodal': None,
            'scorer': None,
            'forgetter': None,
            'learner': None,
            'task_bridge': None,
            'ocr2': None
        }

        if self.vector_store:
            status['vector_store'] = self.vector_store.get_stats()

        if self.ontology_bridge:
            status['ontology'] = {
                'entities': len(self.ontology_bridge.ontology.entities),
                'links': len(self.ontology_bridge.links)
            }

        if self.brain_sync:
            status['brain_sync'] = self.brain_sync.get_sync_status()

        if self.multimodal_store:
            status['multimodal'] = self.multimodal_store.get_stats()

        if self.scorer:
            status['scorer'] = {
                'access_stats': len(self.scorer.access_stats),
                'feedback_stats': len(self.scorer.feedback_stats)
            }

        if self.forgetter:
            status['forgetter'] = self.forgetter.get_stats()

        if self.learner:
            status['learner'] = self.learner.get_stats()

        if self.task_bridge:
            status['task_bridge'] = {
                'links': len(self.task_bridge.links)
            }

        if self.ocr2:
            status['ocr2'] = self.ocr2.get_stats()

        return status

    def health_check(self) -> Dict[str, Any]:
        """
        健康检查(统一报告所有子模块)

        Returns:
            各模块健康状态
        """
        result = {
            'vector_store': self.vector_store is not None,
            'ontology_bridge': self.ontology_bridge is not None,
            'brain_sync': self.brain_sync is not None,
            'multimodal_store': self.multimodal_store is not None,
            'scorer': self.scorer is not None,
            'forgetter': self.forgetter is not None,
            'learner': self.learner is not None,
            'task_bridge': self.task_bridge is not None,
            'ocr2': self.ocr2 is not None,
            'memory_v2': self.memory_v2 is not None,
            'llm_flash': self.llm_flash is not None,
            'llm_pro': self.llm_pro is not None,
        }
        # 如果 memory_v2 可用,追加它自己的 health 信息
        if self.memory_v2:
            try:
                v2_health = self.memory_v2.health_check()
                result['memory_v2_details'] = v2_health
                if v2_health.get("issues"):
                    result['memory_v2_issues'] = v2_health["issues"]
            except Exception as e:
                result['memory_v2_details'] = str(e)
        return result


# 全局实例
_instance = None

def get_xiaoyi_claw(config: Optional[Dict] = None) -> XiaoYiClawLLM:
    """获取小艺 Claw 实例"""
    global _instance
    if _instance is None:
        _instance = XiaoYiClawLLM(config)
    return _instance


# 便捷 API 函数
def remember(content: str, **kwargs) -> str:
    """存储记忆"""
    return get_xiaoyi_claw().remember(content, **kwargs)

def recall(query: str, **kwargs) -> List[Dict]:
    """检索记忆"""
    return get_xiaoyi_claw().recall(query, **kwargs)

def forget(memory_id: str) -> int:
    """删除记忆"""
    return get_xiaoyi_claw().forget(memory_id)

def get_entity(name: str) -> Dict:
    """获取实体"""
    return get_xiaoyi_claw().get_entity(name)

def learn(feedback: Dict) -> bool:
    """学习反馈"""
    return get_xiaoyi_claw().learn(feedback)


# ==================== memory_unified 能力合并 ====================

def recall_yaoyao_memories(query: str, max_results: int = 10) -> List[Dict]:
    """
    读取 yaoyao-memory-v2 每日记忆文件(原 memory_unified._recall_yaoyao 能力)

    Args:
        query: 查询文本
        max_results: 最大返回数

    Returns:
        匹配的记忆列表
    """
    results = []
    yaoyao_dir = Path.home() / ".openclaw" / "workspace" / "memory"
    if not yaoyao_dir.exists():
        return []

    query_lower = query.lower()
    query_words = set(query_lower.split())

    for f in sorted(yaoyao_dir.glob("*.md"), reverse=True)[:10]:
        try:
            content = f.read_text(encoding="utf-8")
            # 简单关键词匹配
            if query_lower in content.lower() or any(w in content.lower() for w in query_words):
                score = sum(content.lower().count(w) for w in query_words) / max(len(query_words), 1)
                results.append({
                    "id": f.stem,
                    "content": content[:500],
                    "source": "yaoyao",
                    "score": min(score * 0.1, 0.9)
                })
        except Exception:
            continue

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:max_results]
