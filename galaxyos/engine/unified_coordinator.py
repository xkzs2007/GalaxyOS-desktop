#!/usr/bin/env python3
"""
小艺 Claw 统一协调器 V2 (Unified Coordinator V2)

整合所有 78 个未使用模块，形成完整的记忆增强系统。

Author: 小艺 Claw
Version: 2.0.0
Created: 2026-04-21
"""

import sys
import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging
from galaxyos.shared.paths import workspace

# 添加模块路径 - 使用绝对路径
CORE_DIR = Path(__file__).parent.resolve()
# PRIVILEGED_DIR 指向 llm-memory-integration 的 src/privileged 目录
PRIVILEGED_DIR = CORE_DIR  # 已合并到本地
# 也检查 xiaoyi-claw-omega-final 下的路径
ALT_PRIVILEGED_DIR = Path(__file__).parent.parent / "src/privileged"
if ALT_PRIVILEGED_DIR.exists():
    PRIVILEGED_DIR = ALT_PRIVILEGED_DIR.resolve()
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(PRIVILEGED_DIR))

logger = logging.getLogger(__name__)


class ModuleType(Enum):
    """模块类型 - 扩展版"""
    # Layer 1: 记忆核心层
    MEMORY_REFLECTION = "memory_reflection"
    SYNAPSE_NETWORK = "synapse_network"
    EMOTION_MEMORY = "emotion_memory"
    ADAPTIVE_MEMORY = "adaptive_memory"
    HALLUCINATION_GUARD = "hallucination_guard"

    # Layer 1.5: 新增核心模块
    MEMGPT_MEMORY = "memgpt_memory"
    GENERATIVE_AGENTS = "generative_agents"
    SELF_RAG = "self_rag"
    KNOWLEDGE_GRAPH_GNN = "knowledge_graph_gnn"

    # Layer 1.7: NLP 处理层
    NLP_PROCESSOR = "nlp_processor"
    NLP_INTEGRATION = "nlp_integration"

    # Layer 1.6: 集成与管理模块
    RULES_MANAGER = "rules_manager"
    AUTONOMOUS_INTEGRATOR = "autonomous_integrator"
    FULL_INTEGRATION = "full_integration"
    XIAOYI_MEMORY_V2 = "xiaoyi_memory_v2"
    OPTIMIZATION_INTEGRATION = "optimization_integration"
    HEARTBEAT_EXECUTOR = "heartbeat_executor"
    ENHANCED_HALLUCINATION = "enhanced_hallucination"

    # Layer 2: 检索增强层
    CRAG_PIPELINE = "crag_pipeline"
    HYBRID_SEARCH = "hybrid_search"
    PROPOSITION_RETRIEVAL = "proposition_retrieval"
    LATE_CHUNKING = "late_chunking"
    RAG_CACHE = "rag_cache"
    RAG_FAILURE_DETECTOR = "rag_failure_detector"
    RAG_OPTIMIZER = "rag_optimizer"
    DISTRIBUTED_SEARCH = "distributed_search"
    MULTIMODAL_SEARCH = "multimodal_search"
    MULTIRESOLUTION_SEARCH = "multiresolution_search"
    CROSS_LINGUAL = "cross_lingual"

    # Layer 3: 向量优化层
    ANN_SELECTOR = "ann_selector"
    SPARSE_ANNS = "sparse_anns"
    OPQ_QUANTIZATION = "opq_quantization"
    VECTOR_QUANTIZATION = "vector_quantization"
    VECTOR_API = "vector_api"

    # Layer 4: LLM 优化层
    SPECULATIVE_DECODER = "speculative_decoder"
    STREAMING_LLM = "streaming_llm"
    LLM_CLIENT = "llm_client"
    LLM_STREAMING = "llm_streaming"
    MODEL_ROUTER = "model_router"
    MODEL_PERFORMANCE = "model_performance"

    # Layer 5: 缓存管理层
    CACHE_ALLOCATOR = "cache_allocator"
    CACHE_AWARE_SCHEDULER = "cache_aware_scheduler"
    APPROXIMATE_CACHE = "approximate_cache"
    UNIFIED_CACHE = "unified_cache"
    SEMANTIC_CACHE = "semantic_cache"
    COMPUTATIONAL_STORAGE = "computational_storage"

    # Layer 6: 硬件优化层
    GPU_OPTIMIZER = "gpu_optimizer"
    NUMA_OPTIMIZER = "numa_optimizer"
    CXL_OPTIMIZER = "cxl_optimizer"
    HARDWARE_OPTIMIZE = "hardware_optimize"
    MKL_ACCELERATOR = "mkl_accelerator"
    FMA_ACCELERATOR = "fma_accelerator"
    KUNPENG_OPTIMIZER = "kunpeng_optimizer"
    HUGE_PAGE_MANAGER = "huge_page_manager"
    IO_OPTIMIZER = "io_optimizer"
    POWER_MANAGER = "power_manager"
    REALTIME_SCHEDULER = "realtime_scheduler"
    IRQ_ISOLATOR = "irq_isolator"
    ZRAM_DETECTOR = "zram_detector"

    # Layer 7: 模块协调层
    MODULE_COORDINATOR = "module_coordinator"
    RESOURCE_ORCHESTRATOR = "resource_orchestrator"
    TOOLS_REGISTRY = "tools_registry"
    AUTO_TUNER = "auto_tuner"

    # Layer 8: 系统可靠性层
    FAILOVER = "failover"
    FULL_RECOVERY = "full_recovery"
    SANDBOX_MANAGER = "sandbox_manager"
    SAFETY_ALIGNMENT = "safety_alignment"
    EXCEPTIONS = "exceptions"

    # Layer 9: 会话管理层
    DAG_CONTEXT_MANAGER = "dag_context_manager"
    CONVERSATION = "conversation"
    CONTEXT_COMPRESSOR = "context_compressor"
    ACP_SERVER = "acp_server"

    # Layer 10: Persona 管理层
    AUTO_UPDATE_PERSONA = "auto_update_persona"
    UPDATE_PERSONA = "update_persona"
    UPDATE_L3_PROFILE = "update_l3_profile"
    SMART_MEMORY_UPDATE = "smart_memory_update"

    # Layer 11: 思考技能
    THINKING_SKILLS = "thinking_skills"

    # Layer 12: 工程与效率技能（Matt Pocock Skills 适配）
    ENGINEERING_SKILLS = "engineering_skills"
    PRODUCTIVITY_SKILLS = "productivity_skills"

    # Layer 新增类型
    INTELLIGENT_THINKING = "intelligent_thinking"
    RESILIENCE_SYSTEM = "resilience_system"
    EMBEDDING_ENHANCE = "embedding_enhance"
    PERFORMANCE_PATCH = "performance_patch"
    RETRIEVAL_FORMULA = "retrieval_formula"
    SQLITE_VEC = "sqlite_vec"
    PLATFORM_ADAPTER = "platform_adapter"
    ASYNC_OPS = "async_ops"
    HYBRID_MEMORY_SEARCH = "hybrid_memory_search"
    REFLECTION_NL = "reflection_nl"

    # 新论文模块（2026-06-14）
    ENGRAM_MEMORY = "engram_memory"
    KAN_NETWORK = "kan_network"
    NEURAL_ODE = "neural_ode"
    LIQUID_FRAMEWORK = "liquid_framework"
    ENGRAM_HEAT_INTEGRATION = "engram_heat_integration"

    # 第二批论文模块（2026-06-14）
    LFM_OPERATOR = "lfm_operator"
    LFM_EDGE = "lfm_edge"
    LFM_ENGRAM_FUSION = "lfm_engram_fusion"
    LGTC_NETWORK = "lgtc_network"
    MAMBA3_SSM = "mamba3_ssm"
    LIQUID_SSM = "liquid_ssm"
    SSM_KAN_FUSION = "ssm_kan_fusion"
    ODE_RNN_CONTINUAL = "ode_rnn_continual"
    NCD_DERIVATIVE = "ncd_derivative"
    LIPSCHITZ_LIQUID = "lipschitz_liquid"
    MOE_ENGRAM_HYBRID = "moe_engram_hybrid"
    UNIFIED_SPARSITY = "unified_sparsity"
    LIQUID_WEIGHT = "liquid_weight"
    DAG_LIQUID = "dag_liquid"


@dataclass
class ModuleInfo:
    """模块信息"""
    name: str
    module_type: ModuleType
    description: str
    triggers: List[str]
    script_path: str
    layer: int = 1
    dependencies: List[str] = field(default_factory=list)
    enabled: bool = True


# 模块注册表 - 完整版
MODULE_REGISTRY: Dict[str, ModuleInfo] = {
    # ==================== Layer 1: 记忆核心层 ====================
    "memory_reflector": ModuleInfo(
        name="memory_reflector",
        module_type=ModuleType.MEMORY_REFLECTION,
        description="记忆反思模块 - 错误检测、模式识别、自动改进",
        triggers=["不对", "错了", "最近有什么问题", "帮我改进"],
        script_path=str(CORE_DIR / "memory_reflector.py"),
        layer=1
    ),
    "synapse_network": ModuleInfo(
        name="synapse_network",
        module_type=ModuleType.SYNAPSE_NETWORK,
        description="记忆突触网络 - 神经元、突触、LTP/LTD、激活传播",
        triggers=["关联记忆", "联想", "记忆连接", "突触"],
        script_path=str(CORE_DIR / "memory_synapse_network.py"),
        layer=1
    ),
    "emotion_memory": ModuleInfo(
        name="emotion_memory",
        module_type=ModuleType.EMOTION_MEMORY,
        description="情感驱动记忆 - 情绪检测、权重计算、优先级分配",
        triggers=["开心", "生气", "焦虑", "情绪"],
        script_path=str(CORE_DIR / "emotion_memory.py"),
        layer=1
    ),
    "adaptive_memory": ModuleInfo(
        name="adaptive_memory",
        module_type=ModuleType.ADAPTIVE_MEMORY,
        description="自适应记忆架构 - 性能分析、参数优化、持续改进",
        triggers=["优化参数", "调整配置", "自适应"],
        script_path=str(CORE_DIR / "adaptive_memory.py"),
        layer=1
    ),
    "hallucination_guard": ModuleInfo(
        name="hallucination_guard",
        module_type=ModuleType.HALLUCINATION_GUARD,
        description="防幻觉守护系统 - 自熟悉度、来源溯源、冲突检测",
        triggers=["验证", "确定吗", "可靠吗", "是真的吗"],
        script_path=str(CORE_DIR / "hallucination_guard.py"),
        layer=1
    ),

    # ==================== Layer 1.5: 新增核心模块 ====================
    "memgpt_memory": ModuleInfo(
        name="memgpt_memory",
        module_type=ModuleType.MEMGPT_MEMORY,
        description="MemGPT 风格记忆 - 三级内存架构（Core/Working/Archival）",
        triggers=["三级内存", "核心记忆", "工作记忆", "归档记忆", "MemGPT"],
        script_path=str(CORE_DIR / "memgpt_memory.py"),
        layer=1,
        dependencies=["memory_functions", "context_compressor", "memory_bank"]
    ),
    "generative_agents": ModuleInfo(
        name="generative_agents",
        module_type=ModuleType.GENERATIVE_AGENTS,
        description="Generative Agents 反思机制 - 记忆流、检索公式、反思引擎、规划引擎",
        triggers=["反思", "规划", "记忆流", "重要性评分", "行动计划"],
        script_path=str(CORE_DIR / "memory_stream.py"),
        layer=1,
        dependencies=["retrieval_formula", "reflection_engine", "planning_engine"]
    ),
    "self_rag": ModuleInfo(
        name="self_rag",
        module_type=ModuleType.SELF_RAG,
        description="Self-RAG 自适应检索 - IsREL/IsSUP/IsUSE 预测器、检索评估、知识精炼",
        triggers=["自适应检索", "检索必要性", "检索支持度", "生成可靠性", "Self-RAG"],
        script_path=str(CORE_DIR / "self_rag.py"),
        layer=1,
        dependencies=["isrel_predictor", "issup_predictor", "isuse_predictor", "retrieval_evaluator", "knowledge_refiner"]
    ),
    "knowledge_graph_gnn": ModuleInfo(
        name="knowledge_graph_gnn",
        module_type=ModuleType.KNOWLEDGE_GRAPH_GNN,
        description="知识图谱 GNN - GraphSAGE/GAT、实体嵌入、关系预测、图谱补全",
        triggers=["知识图谱", "图神经网络", "实体关系", "GraphSAGE", "GAT", "关系预测"],
        script_path=str(CORE_DIR / "knowledge_graph_gnn.py"),
        layer=1,
        dependencies=["graph_constructor", "graphsage_layer", "gat_layer", "relation_predictor"]
    ),

    # ==================== Layer 1.6: 集成与管理模块 ====================
    "rules_manager": ModuleInfo(
        name="rules_manager",
        module_type=ModuleType.RULES_MANAGER,
        description="规则整合管理器 - 整合 AGENTS/MEMORY/TOOLS/SOUL 等规则文件",
        triggers=["规则管理", "规则验证", "规则导出", "规则摘要"],
        script_path=str(CORE_DIR / "rules_manager.py"),
        layer=1,
        dependencies=[]
    ),
    "autonomous_integrator": ModuleInfo(
        name="autonomous_integrator",
        module_type=ModuleType.AUTONOMOUS_INTEGRATOR,
        description="自主任务集成器 - 整合 proactive-tasks/brain/today-task 等外部系统",
        triggers=["自主任务", "任务集成", "外部系统"],
        script_path=str(CORE_DIR / "autonomous_integrator.py"),
        layer=1,
        dependencies=["proactive_tasks", "brain", "today_task"]
    ),
    "lfm_engram_full_integration": ModuleInfo(
        name="lfm_engram_full_integration",
        module_type=ModuleType.ENGRAM_MEMORY,
        description="LFM→Engram 门控融合",
        triggers=["LFM集成", "Engram集成"],
        script_path=str(CORE_DIR / "lfm_engram_fusion.py"),
        layer=1,
    ),
    "lfm_full_integration": ModuleInfo(
        name="lfm_full_integration",
        module_type=ModuleType.ENGRAM_MEMORY,
        description="LFM 全链路 14 模块集成桥 — ODE-RNN/Neural ODE/KAN/LTC/MoE/SSM/Lipschitz/Sparsity/Edge/NCD/DAG",
        triggers=["LFM全链路", "液态集成", "全模块"],
        script_path=str(CORE_DIR / "lfm_full_integration.py"),
        layer=1,
    ),
    "xiaoyi_claw_api": ModuleInfo(
        name="xiaoyi_claw_api",
        module_type=ModuleType.FULL_INTEGRATION,
        description="统一 API 层 - smart_recall/smart_answer/enhanced_recall",
        triggers=["统一入口", "smart_recall", "smart_answer"],
        script_path=str(CORE_DIR / "xiaoyi_claw_api.py"),
        layer=1,
        dependencies=["crag", "retrieval_hub", "skill_coordinator", "autonomous_integrator"]
    ),
    "xiaoyi_memory_v2": ModuleInfo(
        name="xiaoyi_memory_v2",
        module_type=ModuleType.XIAOYI_MEMORY_V2,
        description="统一入口 V2 - smart_recall/smart_answer 统一接口",
        triggers=["统一入口", "smart_recall", "smart_answer"],
        script_path=str(CORE_DIR / "xiaoyi_memory.py"),
        layer=1,
        dependencies=["xiaoyi_claw_api", "memgpt_memory", "self_rag"]
    ),
    "optimization_integration": ModuleInfo(
        name="optimization_integration",
        module_type=ModuleType.OPTIMIZATION_INTEGRATION,
        description="优化模块集成 - 防幻觉参数/CRAG阈值/LTP-LTD/RRF/思考触发",
        triggers=["优化集成", "参数优化", "自适应"],
        script_path=str(CORE_DIR / "optimization_integration.py"),
        layer=1,
    ),

    # ==================== Layer 1.7: NLP 处理层 ====================
    "nlp_processor": ModuleInfo(
        name="nlp_processor",
        module_type=ModuleType.NLP_PROCESSOR,
        description="NLP 处理器 - 中文分词/词性标注/命名实体识别/关键词提取/情感分析/文本摘要",
        triggers=["分词", "实体识别", "关键词", "情感分析", "摘要", "NLP"],
        script_path=str(CORE_DIR / "nlp_processor.py"),
        layer=1,
        dependencies=["jieba", "snownlp"]
    ),
    "nlp_integration": ModuleInfo(
        name="nlp_integration",
        module_type=ModuleType.NLP_INTEGRATION,
        description="NLP 整合器 - 为记忆/检索/防幻觉/知识图谱提供 NLP 能力",
        triggers=["NLP整合", "记忆关键词", "检索分词", "情感检测", "三元组提取"],
        script_path=str(CORE_DIR / "nlp_integration.py"),
        layer=1,
        dependencies=["nlp_processor"]
    ),
    "optimization_integration": ModuleInfo(
        name="optimization_integration",
        module_type=ModuleType.OPTIMIZATION_INTEGRATION,
        description="优化模块集成 - 防幻觉参数自适应/CRAG动态阈值/LTP/LTD自适应/RRF自适应",
        triggers=["优化集成", "参数自适应", "动态阈值"],
        script_path=str(CORE_DIR / "optimization_integration.py"),
        layer=1,
        dependencies=["adaptive_hallucination_params", "dynamic_crag_threshold", "adaptive_ltp_ltd", "adaptive_rrf"]
    ),
    "heartbeat_task_executor": ModuleInfo(
        name="heartbeat_task_executor",
        module_type=ModuleType.HEARTBEAT_EXECUTOR,
        description="心跳任务执行器 - 主动任务/记忆维护/健康检查/技能更新/备份",
        triggers=["心跳执行", "心跳任务", "后台任务"],
        script_path=str(CORE_DIR / "heartbeat_task_executor.py"),
        layer=1,
        dependencies=["autonomous_integrator", "rules_manager"]
    ),
    "enhanced_hallucination_guard": ModuleInfo(
        name="enhanced_hallucination_guard",
        module_type=ModuleType.ENHANCED_HALLUCINATION,
        description="增强版防幻觉 - 多源交叉验证/思考能力增强/渐进式验证",
        triggers=["增强防幻觉", "交叉验证", "思考增强"],
        script_path=str(CORE_DIR / "enhanced_hallucination_guard.py"),
        layer=1,
        dependencies=["hallucination_guard", "skill_coordinator"]
    ),

    # ==================== Layer 2: 检索增强层 ====================
    "crag_pipeline": ModuleInfo(
        name="crag_pipeline",
        module_type=ModuleType.CRAG_PIPELINE,
        description="CRAG 纠错检索 - 检索评估、纠正、补充",
        triggers=["纠错检索", "CRAG", "检索纠正"],
        script_path=str(PRIVILEGED_DIR / "crag_pipeline.py"),
        layer=2,
        dependencies=["hybrid_search", "rag_failure_detector"]
    ),
    "hybrid_search": ModuleInfo(
        name="hybrid_search",
        module_type=ModuleType.HYBRID_SEARCH,
        description="混合检索 - Dense + Sparse + RRF 融合",
        triggers=["混合检索", "hybrid", "BM25"],
        script_path=str(PRIVILEGED_DIR / "hybrid_search.py"),
        layer=2
    ),
    "proposition_retrieval": ModuleInfo(
        name="proposition_retrieval",
        module_type=ModuleType.PROPOSITION_RETRIEVAL,
        description="命题检索 - 原子命题级检索粒度",
        triggers=["命题检索", "proposition"],
        script_path=str(PRIVILEGED_DIR / "proposition_retriever.py"),
        layer=2
    ),
    "late_chunking": ModuleInfo(
        name="late_chunking",
        module_type=ModuleType.LATE_CHUNKING,
        description="延迟分块 - 上下文感知分块",
        triggers=["延迟分块", "late chunking"],
        script_path=str(PRIVILEGED_DIR / "late_chunking.py"),
        layer=2
    ),
    "rag_cache": ModuleInfo(
        name="rag_cache",
        module_type=ModuleType.RAG_CACHE,
        description="RAG 缓存 - 知识树、LRU-K 缓存",
        triggers=["RAG缓存", "知识树"],
        script_path=str(PRIVILEGED_DIR / "rag_cache.py"),
        layer=2
    ),
    "rag_failure_detector": ModuleInfo(
        name="rag_failure_detector",
        module_type=ModuleType.RAG_FAILURE_DETECTOR,
        description="RAG 失败检测 - 7 种失败点检测",
        triggers=["RAG失败", "检索失败"],
        script_path=str(PRIVILEGED_DIR / "rag_failure_detector.py"),
        layer=2
    ),
    "rag_optimizer": ModuleInfo(
        name="rag_optimizer",
        module_type=ModuleType.RAG_OPTIMIZER,
        description="RAG 优化器 - 参数调优、性能优化",
        triggers=["RAG优化", "检索优化"],
        script_path=str(PRIVILEGED_DIR / "rag_optimizer.py"),
        layer=2
    ),
    "distributed_search": ModuleInfo(
        name="distributed_search",
        module_type=ModuleType.DISTRIBUTED_SEARCH,
        description="分布式检索 - 向量分片、并行检索",
        triggers=["分布式检索", "分片检索"],
        script_path=str(PRIVILEGED_DIR / "distributed_search.py"),
        layer=2
    ),
    "multimodal_search": ModuleInfo(
        name="multimodal_search",
        module_type=ModuleType.MULTIMODAL_SEARCH,
        description="多模态检索 - 图文混合检索",
        triggers=["多模态检索", "图文检索"],
        script_path=str(PRIVILEGED_DIR / "multimodal_search.py"),
        layer=2
    ),
    "multiresolution_search": ModuleInfo(
        name="multiresolution_search",
        module_type=ModuleType.MULTIRESOLUTION_SEARCH,
        description="多分辨率检索 - 粗细粒度结合",
        triggers=["多分辨率", "粗细检索"],
        script_path=str(PRIVILEGED_DIR / "multiresolution_search.py"),
        layer=2
    ),
    "cross_lingual": ModuleInfo(
        name="cross_lingual",
        module_type=ModuleType.CROSS_LINGUAL,
        description="跨语言检索 - 多语言支持",
        triggers=["跨语言", "多语言检索"],
        script_path=str(PRIVILEGED_DIR / "cross_lingual.py"),
        layer=2
    ),

    # ==================== Layer 3: 向量优化层 ====================
    "ann_selector": ModuleInfo(
        name="ann_selector",
        module_type=ModuleType.ANN_SELECTOR,
        description="ANN 算法选择器 - 自动选择最优 ANN 算法",
        triggers=["ANN选择", "算法选择"],
        script_path=str(PRIVILEGED_DIR / "ann_selector.py"),
        layer=3
    ),
    "sparse_anns": ModuleInfo(
        name="sparse_anns",
        module_type=ModuleType.SPARSE_ANNS,
        description="稀疏 ANN - 稀疏向量索引",
        triggers=["稀疏索引", "sparse ANN"],
        script_path=str(PRIVILEGED_DIR / "sparse_anns.py"),
        layer=3
    ),
    "opq_quantization": ModuleInfo(
        name="opq_quantization",
        module_type=ModuleType.OPQ_QUANTIZATION,
        description="OPQ 量化 - 优化乘积量化",
        triggers=["OPQ量化", "乘积量化"],
        script_path=str(PRIVILEGED_DIR / "opq_quantization.py"),
        layer=3
    ),
    "vector_quantization": ModuleInfo(
        name="vector_quantization",
        module_type=ModuleType.VECTOR_QUANTIZATION,
        description="向量量化 - INT8/FP16 量化",
        triggers=["向量量化", "量化压缩"],
        script_path=str(PRIVILEGED_DIR / "quantization.py"),
        layer=3
    ),
    "vector_api": ModuleInfo(
        name="vector_api",
        module_type=ModuleType.VECTOR_API,
        description="向量 API - 统一向量操作接口",
        triggers=["向量API", "向量操作"],
        script_path=str(PRIVILEGED_DIR / "vector_api.py"),
        layer=3
    ),

    # ==================== Layer 4: LLM 优化层 ====================
    "speculative_decoder": ModuleInfo(
        name="speculative_decoder",
        module_type=ModuleType.SPECULATIVE_DECODER,
        description="投机解码 - 2-3x 推理加速",
        triggers=["投机解码", "加速推理"],
        script_path=str(PRIVILEGED_DIR / "speculative_decoder.py"),
        layer=4
    ),
    "streaming_llm": ModuleInfo(
        name="streaming_llm",
        module_type=ModuleType.STREAMING_LLM,
        description="流式 LLM - KV Cache 管理",
        triggers=["流式LLM", "KV缓存"],
        script_path=str(PRIVILEGED_DIR / "streaming_llm.py"),
        layer=4
    ),
    "llm_client": ModuleInfo(
        name="llm_client",
        module_type=ModuleType.LLM_CLIENT,
        description="LLM 客户端 - 统一 LLM 调用接口",
        triggers=["LLM客户端", "模型调用"],
        script_path=str(PRIVILEGED_DIR / "llm_client.py"),
        layer=4
    ),
    "llm_streaming": ModuleInfo(
        name="llm_streaming",
        module_type=ModuleType.LLM_STREAMING,
        description="LLM 流式处理 - 异步流式生成",
        triggers=["流式生成", "异步LLM"],
        script_path=str(PRIVILEGED_DIR / "llm_streaming.py"),
        layer=4
    ),
    "model_router": ModuleInfo(
        name="model_router",
        module_type=ModuleType.MODEL_ROUTER,
        description="模型路由 - 智能选择最优模型",
        triggers=["模型路由", "模型选择"],
        script_path=str(PRIVILEGED_DIR / "model_router.py"),
        layer=4
    ),
    "model_performance": ModuleInfo(
        name="model_performance",
        module_type=ModuleType.MODEL_PERFORMANCE,
        description="模型性能监控 - 延迟、吞吐量追踪",
        triggers=["模型性能", "性能监控"],
        script_path=str(PRIVILEGED_DIR / "model_performance.py"),
        layer=4
    ),

    # ==================== Layer 5: 缓存管理层 ====================
    "cache_allocator": ModuleInfo(
        name="cache_allocator",
        module_type=ModuleType.CACHE_ALLOCATOR,
        description="缓存分配器 - 智能缓存分配",
        triggers=["缓存分配", "内存分配"],
        script_path=str(PRIVILEGED_DIR / "cache_allocator.py"),
        layer=5
    ),
    "cache_aware_scheduler": ModuleInfo(
        name="cache_aware_scheduler",
        module_type=ModuleType.CACHE_AWARE_SCHEDULER,
        description="缓存感知调度 - NUMA 感知调度",
        triggers=["缓存调度", "NUMA调度"],
        script_path=str(PRIVILEGED_DIR / "cache_aware_scheduler.py"),
        layer=5
    ),
    "approximate_cache": ModuleInfo(
        name="approximate_cache",
        module_type=ModuleType.APPROXIMATE_CACHE,
        description="近似缓存 - LSH 近似查找",
        triggers=["近似缓存", "LSH缓存"],
        script_path=str(PRIVILEGED_DIR / "approximate_cache.py"),
        layer=5
    ),
    "unified_cache": ModuleInfo(
        name="unified_cache",
        module_type=ModuleType.UNIFIED_CACHE,
        description="统一缓存 - 多级缓存统一管理",
        triggers=["统一缓存", "多级缓存"],
        script_path=str(PRIVILEGED_DIR / "unified_cache.py"),
        layer=5
    ),
    "semantic_cache": ModuleInfo(
        name="semantic_cache",
        module_type=ModuleType.SEMANTIC_CACHE,
        description="语义缓存 - 基于语义相似度的缓存",
        triggers=["语义缓存", "相似缓存"],
        script_path=str(PRIVILEGED_DIR / "semantic_cache.py"),
        layer=5
    ),
    "computational_storage": ModuleInfo(
        name="computational_storage",
        module_type=ModuleType.COMPUTATIONAL_STORAGE,
        description="计算存储 - KV Cache 管理、量化",
        triggers=["计算存储", "KV管理"],
        script_path=str(PRIVILEGED_DIR / "computational_storage.py"),
        layer=5
    ),

    # ==================== Layer 6: 硬件优化层 ====================
    "gpu_optimizer": ModuleInfo(
        name="gpu_optimizer",
        module_type=ModuleType.GPU_OPTIMIZER,
        description="GPU 优化 - CUDA/OpenCL 加速",
        triggers=["GPU优化", "CUDA加速"],
        script_path=str(PRIVILEGED_DIR / "gpu_optimizer.py"),
        layer=6
    ),
    "numa_optimizer": ModuleInfo(
        name="numa_optimizer",
        module_type=ModuleType.NUMA_OPTIMIZER,
        description="NUMA 优化 - 内存亲和性优化",
        triggers=["NUMA优化", "内存亲和"],
        script_path=str(PRIVILEGED_DIR / "numa_optimizer.py"),
        layer=6
    ),
    "cxl_optimizer": ModuleInfo(
        name="cxl_optimizer",
        module_type=ModuleType.CXL_OPTIMIZER,
        description="CXL 内存优化 - CXL 内存池管理",
        triggers=["CXL优化", "内存池"],
        script_path=str(PRIVILEGED_DIR / "cxl_optimizer.py"),
        layer=6
    ),
    "hardware_optimize": ModuleInfo(
        name="hardware_optimize",
        module_type=ModuleType.HARDWARE_OPTIMIZE,
        description="硬件优化 - CPU/GPU/NPU 统一优化",
        triggers=["硬件优化", "硬件加速"],
        script_path=str(PRIVILEGED_DIR / "hardware_optimize.py"),
        layer=6
    ),
    "mkl_accelerator": ModuleInfo(
        name="mkl_accelerator",
        module_type=ModuleType.MKL_ACCELERATOR,
        description="MKL 加速 - Intel MKL 数学库加速",
        triggers=["MKL加速", "数学库加速"],
        script_path=str(PRIVILEGED_DIR / "mkl_accelerator.py"),
        layer=6
    ),
    "fma_accelerator": ModuleInfo(
        name="fma_accelerator",
        module_type=ModuleType.FMA_ACCELERATOR,
        description="FMA 加速 - 融合乘加指令加速",
        triggers=["FMA加速", "向量指令"],
        script_path=str(PRIVILEGED_DIR / "fma_accelerator.py"),
        layer=6
    ),
    "kunpeng_optimizer": ModuleInfo(
        name="kunpeng_optimizer",
        module_type=ModuleType.KUNPENG_OPTIMIZER,
        description="鲲鹏优化 - ARM 架构优化",
        triggers=["鲲鹏优化", "ARM优化"],
        script_path=str(PRIVILEGED_DIR / "kunpeng_optimizer.py"),
        layer=6
    ),
    "huge_page_manager": ModuleInfo(
        name="huge_page_manager",
        module_type=ModuleType.HUGE_PAGE_MANAGER,
        description="大页内存管理 - HugePages 优化",
        triggers=["大页内存", "HugePages"],
        script_path=str(PRIVILEGED_DIR / "hugepage_manager.py"),
        layer=6
    ),
    "io_optimizer": ModuleInfo(
        name="io_optimizer",
        module_type=ModuleType.IO_OPTIMIZER,
        description="IO 优化 - 磁盘/网络 IO 优化",
        triggers=["IO优化", "磁盘优化"],
        script_path=str(PRIVILEGED_DIR / "io_optimizer.py"),
        layer=6
    ),
    "power_manager": ModuleInfo(
        name="power_manager",
        module_type=ModuleType.POWER_MANAGER,
        description="电源管理 - DVFS、功耗控制",
        triggers=["电源管理", "功耗控制"],
        script_path=str(PRIVILEGED_DIR / "power_manager.py"),
        layer=6
    ),
    "realtime_scheduler": ModuleInfo(
        name="realtime_scheduler",
        module_type=ModuleType.REALTIME_SCHEDULER,
        description="实时调度 - RT 调度、优先级管理",
        triggers=["实时调度", "RT调度"],
        script_path=str(PRIVILEGED_DIR / "realtime_scheduler.py"),
        layer=6
    ),
    "irq_isolator": ModuleInfo(
        name="irq_isolator",
        module_type=ModuleType.IRQ_ISOLATOR,
        description="IRQ 隔离 - 中断隔离优化",
        triggers=["IRQ隔离", "中断隔离"],
        script_path=str(PRIVILEGED_DIR / "irq_isolator.py"),
        layer=6
    ),
    "zram_detector": ModuleInfo(
        name="zram_detector",
        module_type=ModuleType.ZRAM_DETECTOR,
        description="ZRAM 检测 - 压缩内存检测",
        triggers=["ZRAM", "压缩内存"],
        script_path=str(PRIVILEGED_DIR / "zram_detector.py"),
        layer=6
    ),

    # ==================== Layer 7: 模块协调层 ====================
    "module_coordinator": ModuleInfo(
        name="module_coordinator",
        module_type=ModuleType.MODULE_COORDINATOR,
        description="模块协调器 - 进程管理、模块编排",
        triggers=["模块协调", "进程管理"],
        script_path=str(PRIVILEGED_DIR / "module_coordinator.py"),
        layer=7
    ),
    "resource_orchestrator": ModuleInfo(
        name="resource_orchestrator",
        module_type=ModuleType.RESOURCE_ORCHESTRATOR,
        description="资源编排 - CPU/内存/GPU 统一调度",
        triggers=["资源编排", "资源调度"],
        script_path=str(PRIVILEGED_DIR / "resource_orchestrator.py"),
        layer=7
    ),
    "tools_registry": ModuleInfo(
        name="tools_registry",
        module_type=ModuleType.TOOLS_REGISTRY,
        description="工具注册 - 工具发现、注册、调用",
        triggers=["工具注册", "工具发现"],
        script_path=str(PRIVILEGED_DIR / "tools_registry.py"),
        layer=7
    ),
    "auto_tuner": ModuleInfo(
        name="auto_tuner",
        module_type=ModuleType.AUTO_TUNER,
        description="自动调优 - 参数自动优化",
        triggers=["自动调优", "参数优化"],
        script_path=str(PRIVILEGED_DIR / "auto_tuner.py"),
        layer=7
    ),

    # ==================== Layer 8: 系统可靠性层 ====================
    "failover": ModuleInfo(
        name="failover",
        module_type=ModuleType.FAILOVER,
        description="故障转移 - 健康检查、自动切换",
        triggers=["故障转移", "健康检查"],
        script_path=str(PRIVILEGED_DIR / "failover.py"),
        layer=8
    ),
    "full_recovery": ModuleInfo(
        name="full_recovery",
        module_type=ModuleType.FULL_RECOVERY,
        description="完整恢复 - 数据恢复、状态重建",
        triggers=["数据恢复", "状态恢复"],
        script_path=str(PRIVILEGED_DIR / "full_recovery.py"),
        layer=8
    ),
    "sandbox_manager": ModuleInfo(
        name="sandbox_manager",
        module_type=ModuleType.SANDBOX_MANAGER,
        description="沙箱管理 - 安全隔离、权限控制",
        triggers=["沙箱", "安全隔离"],
        script_path=str(PRIVILEGED_DIR / "sandbox_manager.py"),
        layer=8
    ),
    "safety_alignment": ModuleInfo(
        name="safety_alignment",
        module_type=ModuleType.SAFETY_ALIGNMENT,
        description="安全对齐 - 内容安全、幻觉检测",
        triggers=["安全对齐", "内容安全"],
        script_path=str(PRIVILEGED_DIR / "safety_alignment.py"),
        layer=8
    ),
    "exceptions": ModuleInfo(
        name="exceptions",
        module_type=ModuleType.EXCEPTIONS,
        description="异常定义 - 统一异常处理",
        triggers=[],
        script_path=str(PRIVILEGED_DIR / "exceptions.py"),
        layer=8
    ),

    # ==================== Layer 9: 会话管理层 ====================
    "dag_context_manager": ModuleInfo(
        name="dag_context_manager",
        module_type=ModuleType.DAG_CONTEXT_MANAGER,
        description="DAG 上下文管理器 - DAG节点存储、增量摘要、上下文组装、人格保护、回溯检索",
        triggers=["上下文管理", "DAG", "上下文压缩", "无损上下文", "人格保护", "回溯"],
        script_path=str(PRIVILEGED_DIR / "dag_context_manager.py"),
        layer=9,
        dependencies=["nlp_processor", "mkl_accelerator"]
    ),
    "conversation": ModuleInfo(
        name="conversation",
        module_type=ModuleType.CONVERSATION,
        description="对话管理 - 会话存储、上下文管理",
        triggers=["对话管理", "会话管理"],
        script_path=str(PRIVILEGED_DIR / "conversation.py"),
        layer=9
    ),
    "context_compressor": ModuleInfo(
        name="context_compressor",
        module_type=ModuleType.CONTEXT_COMPRESSOR,
        description="上下文压缩 - LLMLingua 风格压缩",
        triggers=["上下文压缩", "压缩上下文"],
        script_path=str(PRIVILEGED_DIR / "context_compressor.py"),
        layer=9
    ),
    "acp_server": ModuleInfo(
        name="acp_server",
        module_type=ModuleType.ACP_SERVER,
        description="ACP 服务器 - Agent Communication Protocol",
        triggers=["ACP", "Agent通信"],
        script_path=str(PRIVILEGED_DIR / "acp_server.py"),
        layer=9
    ),

    # ==================== Layer 10: Persona 管理层 ====================
    "auto_update_persona": ModuleInfo(
        name="auto_update_persona",
        module_type=ModuleType.AUTO_UPDATE_PERSONA,
        description="自动更新 Persona - 从对话学习",
        triggers=["自动更新", "Persona更新"],
        script_path=str(PRIVILEGED_DIR / "auto_update_persona.py"),
        layer=10
    ),
    "update_persona": ModuleInfo(
        name="update_persona",
        module_type=ModuleType.UPDATE_PERSONA,
        description="更新 Persona - 手动更新接口",
        triggers=["更新Persona"],
        script_path=str(PRIVILEGED_DIR / "update_persona.py"),
        layer=10
    ),
    "update_l3_profile": ModuleInfo(
        name="update_l3_profile",
        module_type=ModuleType.UPDATE_L3_PROFILE,
        description="更新 L3 Profile - 长期记忆更新",
        triggers=["L3更新", "长期记忆更新"],
        script_path=str(PRIVILEGED_DIR / "update_l3_profile.py"),
        layer=10
    ),
    "smart_memory_update": ModuleInfo(
        name="smart_memory_update",
        module_type=ModuleType.SMART_MEMORY_UPDATE,
        description="智能记忆更新 - 增量更新、冲突检测",
        triggers=["智能更新", "增量更新"],
        script_path=str(PRIVILEGED_DIR / "smart_memory_update.py"),
        layer=10
    ),

    # ==================== Layer 11: 思考技能 ====================
    "skill_coordinator": ModuleInfo(
        name="skill_coordinator",
        module_type=ModuleType.THINKING_SKILLS,
        description="技能协调器 - 智能路由、快捷指令、工作流",
        triggers=["从根本上", "系统性", "评估", "倒推", "类比", "解释"],
        script_path=str(CORE_DIR / "skill_coordinator.py"),
        layer=11
    ),

    # ==================== Layer 12: 核心组件（补充注册）====================

    # 防幻觉相关
    "adaptive_hallucination_params": ModuleInfo(
        name="adaptive_hallucination_params",
        module_type=ModuleType.HALLUCINATION_GUARD,
        description="防幻觉参数自适应 - Self-RAG 风格动态调整",
        triggers=["防幻觉参数", "幻觉检测"],
        script_path=str(CORE_DIR / "adaptive_hallucination_params.py"),
        layer=1
    ),
    "adaptive_ltp_ltd": ModuleInfo(
        name="adaptive_ltp_ltd",
        module_type=ModuleType.SYNAPSE_NETWORK,
        description="自适应 LTP/LTD - Hebbian 学习规则",
        triggers=["LTP", "LTD", "突触可塑性"],
        script_path=str(CORE_DIR / "adaptive_ltp_ltd.py"),
        layer=1
    ),
    "adaptive_rrf": ModuleInfo(
        name="adaptive_rrf",
        module_type=ModuleType.HYBRID_SEARCH,
        description="自适应 RRF 融合 - 倒数排名融合权重优化",
        triggers=["RRF", "融合权重"],
        script_path=str(CORE_DIR / "adaptive_rrf.py"),
        layer=2
    ),
    "dynamic_crag_threshold": ModuleInfo(
        name="dynamic_crag_threshold",
        module_type=ModuleType.CRAG_PIPELINE,
        description="CRAG 动态阈值 - 自适应检索置信度",
        triggers=["CRAG阈值", "动态阈值"],
        script_path=str(CORE_DIR / "dynamic_crag_threshold.py"),
        layer=2
    ),
    "hallucination_integration": ModuleInfo(
        name="hallucination_integration",
        module_type=ModuleType.HALLUCINATION_GUARD,
        description="防幻觉集成接口 - 统一防幻觉调用",
        triggers=["防幻觉集成"],
        script_path=str(CORE_DIR / "hallucination_integration.py"),
        layer=1
    ),

    # 检索增强
    "crag": ModuleInfo(
        name="crag",
        module_type=ModuleType.CRAG_PIPELINE,
        description="CRAG 核心模块 - 纠错检索生成",
        triggers=["CRAG", "纠错检索"],
        script_path=str(CORE_DIR / "crag.py"),
        layer=2
    ),
    "isrel_predictor": ModuleInfo(
        name="isrel_predictor",
        module_type=ModuleType.SELF_RAG,
        description="IsREL 预测器 - 检索必要性判断",
        triggers=["IsREL", "检索必要性"],
        script_path=str(CORE_DIR / "isrel_predictor.py"),
        layer=1
    ),
    "issup_predictor": ModuleInfo(
        name="issup_predictor",
        module_type=ModuleType.SELF_RAG,
        description="IsSUP 预测器 - 检索支持度判断",
        triggers=["IsSUP", "检索支持度"],
        script_path=str(CORE_DIR / "issup_predictor.py"),
        layer=1
    ),
    "isuse_predictor": ModuleInfo(
        name="isuse_predictor",
        module_type=ModuleType.SELF_RAG,
        description="IsUSE 预测器 - 生成有用性判断",
        triggers=["IsUSE", "有用性"],
        script_path=str(CORE_DIR / "isuse_predictor.py"),
        layer=1
    ),
    "knowledge_augmentor": ModuleInfo(
        name="knowledge_augmentor",
        module_type=ModuleType.SELF_RAG,
        description="知识补充器 - 检索不足时补充知识",
        triggers=["知识补充"],
        script_path=str(CORE_DIR / "knowledge_augmentor.py"),
        layer=2
    ),
    "knowledge_refiner": ModuleInfo(
        name="knowledge_refiner",
        module_type=ModuleType.SELF_RAG,
        description="知识精炼器 - 过滤噪声知识",
        triggers=["知识精炼"],
        script_path=str(CORE_DIR / "knowledge_refiner.py"),
        layer=2
    ),
    "retrieval_eval": ModuleInfo(
        name="retrieval_eval",
        module_type=ModuleType.CRAG_PIPELINE,
        description="检索评估器 - 评估检索质量",
        triggers=["检索评估"],
        script_path=str(CORE_DIR / "retrieval_eval.py"),
        layer=2
    ),
    "retrieval_evaluator": ModuleInfo(
        name="retrieval_evaluator",
        module_type=ModuleType.SELF_RAG,
        description="检索评估器 V2 - Self-RAG 风格评估",
        triggers=["检索评估V2"],
        script_path=str(CORE_DIR / "retrieval_evaluator.py"),
        layer=2
    ),

    # 知识图谱
    "graph_constructor": ModuleInfo(
        name="graph_constructor",
        module_type=ModuleType.KNOWLEDGE_GRAPH_GNN,
        description="图谱构建器 - 从文本构建知识图谱",
        triggers=["图谱构建", "实体抽取"],
        script_path=str(CORE_DIR / "graph_constructor.py"),
        layer=1
    ),
    "gat_layer": ModuleInfo(
        name="gat_layer",
        module_type=ModuleType.KNOWLEDGE_GRAPH_GNN,
        description="GAT 层 - 图注意力网络",
        triggers=["GAT", "图注意力"],
        script_path=str(CORE_DIR / "gat_layer.py"),
        layer=1
    ),
    "graphsage_layer": ModuleInfo(
        name="graphsage_layer",
        module_type=ModuleType.KNOWLEDGE_GRAPH_GNN,
        description="GraphSAGE 层 - 图采样聚合网络",
        triggers=["GraphSAGE", "图采样"],
        script_path=str(CORE_DIR / "graphsage_layer.py"),
        layer=1
    ),
    "relation_predictor": ModuleInfo(
        name="relation_predictor",
        module_type=ModuleType.KNOWLEDGE_GRAPH_GNN,
        description="关系预测器 - 预测实体间关系",
        triggers=["关系预测"],
        script_path=str(CORE_DIR / "relation_predictor.py"),
        layer=1
    ),



    # 记忆核心
    "memory_bank": ModuleInfo(
        name="memory_bank",
        module_type=ModuleType.MEMGPT_MEMORY,
        description="记忆银行 - 归档存储",
        triggers=["记忆银行", "归档"],
        script_path=str(CORE_DIR / "memory_bank.py"),
        layer=1
    ),
    "memory_functions": ModuleInfo(
        name="memory_functions",
        module_type=ModuleType.MEMGPT_MEMORY,
        description="记忆函数 - 记忆操作工具集",
        triggers=["记忆函数"],
        script_path=str(CORE_DIR / "memory_functions.py"),
        layer=1
    ),
    "memory_ontology_bridge": ModuleInfo(
        name="memory_ontology_bridge",
        module_type=ModuleType.KNOWLEDGE_GRAPH_GNN,
        description="记忆-知识图谱桥接 - 双向同步",
        triggers=["知识图谱桥接"],
        script_path=str(CORE_DIR / "memory_ontology_bridge.py"),
        layer=1
    ),
    "memory_stream": ModuleInfo(
        name="memory_stream",
        module_type=ModuleType.GENERATIVE_AGENTS,
        description="记忆流 - Generative Agents 风格",
        triggers=["记忆流"],
        script_path=str(CORE_DIR / "memory_stream.py"),
        layer=1
    ),
    "memory_synapse_network": ModuleInfo(
        name="memory_synapse_network",
        module_type=ModuleType.SYNAPSE_NETWORK,
        description="记忆突触网络 - 神经元连接",
        triggers=["突触网络"],
        script_path=str(CORE_DIR / "memory_synapse_network.py"),
        layer=1
    ),
    "memory_unified": ModuleInfo(
        name="memory_unified",
        module_type=ModuleType.XIAOYI_MEMORY_V2,
        description="统一记忆接口 - 整合多记忆源",
        triggers=["统一记忆"],
        script_path=str(CORE_DIR / "memory_unified.py"),
        layer=1
    ),
    "multimodal_memory": ModuleInfo(
        name="multimodal_memory",
        module_type=ModuleType.MEMGPT_MEMORY,
        description="多模态记忆 - 图像/视频记忆",
        triggers=["多模态记忆", "图像记忆"],
        script_path=str(CORE_DIR / "multimodal_memory.py"),
        layer=1
    ),

    # 视觉生成
    "visual_generation": ModuleInfo(
        name="visual_generation",
        module_type=ModuleType.THINKING_SKILLS,
        description="视觉生成模块 - 记忆可视化/图谱可视化",
        triggers=["可视化", "图表生成"],
        script_path=str(CORE_DIR / "visual_generation.py"),
        layer=1
    ),

    # 反思/规划
    "reflection_engine": ModuleInfo(
        name="reflection_engine",
        module_type=ModuleType.GENERATIVE_AGENTS,
        description="反思引擎 - Generative Agents 反思机制",
        triggers=["反思", "反思引擎"],
        script_path=str(CORE_DIR / "reflection_engine.py"),
        layer=1
    ),
    "planning_engine": ModuleInfo(
        name="planning_engine",
        module_type=ModuleType.GENERATIVE_AGENTS,
        description="规划引擎 - 行动计划生成",
        triggers=["规划", "行动计划"],
        script_path=str(CORE_DIR / "planning_engine.py"),
        layer=1
    ),

    # 向量存储
    "unified_vector_store": ModuleInfo(
        name="unified_vector_store",
        module_type=ModuleType.VECTOR_API,
        description="统一向量存储 - FAISS/Qdrant/sqlite-vec",
        triggers=["向量存储", "统一存储"],
        script_path=str(CORE_DIR / "unified_vector_store.py"),
        layer=3
    ),
    "vector_store": ModuleInfo(
        name="vector_store",
        module_type=ModuleType.VECTOR_API,
        description="向量存储基础 - 向量操作封装",
        triggers=["向量库"],
        script_path=str(CORE_DIR / "vector_api.py"),
        layer=3
    ),

    # 其他重要模块
    "importance_scorer": ModuleInfo(
        name="importance_scorer",
        module_type=ModuleType.GENERATIVE_AGENTS,
        description="重要性评分器 - 记忆重要性计算",
        triggers=["重要性评分"],
        script_path=str(CORE_DIR / "importance_scorer.py"),
        layer=1
    ),
    "smart_forgetter": ModuleInfo(
        name="smart_forgetter",
        module_type=ModuleType.ADAPTIVE_MEMORY,
        description="智能遗忘 - 基于重要性的遗忘机制",
        triggers=["智能遗忘", "遗忘"],
        script_path=str(CORE_DIR / "smart_forgetter.py"),
        layer=1
    ),
    "auto_learner": ModuleInfo(
        name="auto_learner",
        module_type=ModuleType.ADAPTIVE_MEMORY,
        description="自主学习 - 从反馈中学习",
        triggers=["自主学习", "学习"],
        script_path=str(CORE_DIR / "auto_learner.py"),
        layer=1
    ),
    "task_memory_bridge": ModuleInfo(
        name="task_memory_bridge",
        module_type=ModuleType.AUTONOMOUS_INTEGRATOR,
        description="任务-记忆桥接 - 任务关联记忆",
        triggers=["任务桥接"],
        script_path=str(CORE_DIR / "task_memory_bridge.py"),
        layer=1
    ),
    "brain_memory_sync": ModuleInfo(
        name="brain_memory_sync",
        module_type=ModuleType.AUTONOMOUS_INTEGRATOR,
        description="知识库-记忆同步 - 双向同步",
        triggers=["知识库同步"],
        script_path=str(CORE_DIR / "brain_memory_sync.py"),
        layer=1
    ),
    "quantization": ModuleInfo(
        name="quantization",
        module_type=ModuleType.VECTOR_QUANTIZATION,
        description="向量量化 - INT8/FP16 量化",
        triggers=["量化"],
        script_path=str(CORE_DIR / "quantization.py"),
        layer=3
    ),
    "sqlite_ext": ModuleInfo(
        name="sqlite_ext",
        module_type=ModuleType.VECTOR_API,
        description="SQLite 扩展 - sqlite-vec 封装",
        triggers=["SQLite扩展"],
        script_path=str(CORE_DIR / "sqlite_ext.py"),
        layer=3
    ),
    "dep_checker": ModuleInfo(
        name="dep_checker",
        module_type=ModuleType.EXCEPTIONS,
        description="依赖检查器 - 检查模块依赖",
        triggers=["依赖检查"],
        script_path=str(CORE_DIR / "dep_checker.py"),
        layer=8
    ),
    "lfm_engram_full_integration": ModuleInfo(
        name="lfm_engram_full_integration",
        module_type=ModuleType.ENGRAM_MEMORY,
        description="LFM→Engram 门控融合",
        triggers=["LFM集成", "Engram集成"],
        script_path=str(CORE_DIR / "lfm_engram_fusion.py"),
        layer=1,
    ),
    "lfm_full_integration": ModuleInfo(
        name="lfm_full_integration",
        module_type=ModuleType.ENGRAM_MEMORY,
        description="LFM 全链路 14 模块集成桥 — ODE-RNN/Neural ODE/KAN/LTC/MoE/SSM/Lipschitz/Sparsity/Edge/NCD/DAG",
        triggers=["LFM全链路", "液态集成", "全模块"],
        script_path=str(CORE_DIR / "lfm_full_integration.py"),
        layer=1,
    ),
    "xiaoyi_claw_api": ModuleInfo(
        name="xiaoyi_claw_api",
        module_type=ModuleType.XIAOYI_MEMORY_V2,
        description="小艺 Claw 统一 API - 记忆管理接口",
        triggers=["统一API", "小艺API"],
        script_path=str(CORE_DIR / "xiaoyi_claw_api.py"),
        layer=1
    ),
}


class UnifiedCoordinator:
    """
    统一协调器 V2
    
    整合所有 78 个模块，提供统一的调用接口。
    """

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or
            workspace())
        self.modules = MODULE_REGISTRY
        self._loaded_modules: Dict[str, Any] = {}
        self._layer_cache: Dict[int, List[str]] = {}
        self._workflows: Dict[str, List[Tuple[str, str]]] = {}

        logger.info(f"统一协调器 V2 初始化完成，共 {len(self.modules)} 个模块")

    @property
    def workflows(self) -> Dict[str, List[Tuple[str, str]]]:
        """获取所有工作流"""
        if not self._workflows:
            self._workflows = self._build_workflows()
        return self._workflows

    def _load_module(self, module_name: str) -> Optional[Any]:
        """懒加载模块"""
        if module_name in self._loaded_modules:
            return self._loaded_modules[module_name]

        module_info = self.modules.get(module_name)
        if not module_info:
            logger.warning(f"模块未注册: {module_name}")
            return None

        if not module_info.enabled:
            logger.debug(f"模块已禁用: {module_name}")
            return None

        try:
            import importlib.util
            script_path = Path(module_info.script_path)

            # 如果是相对路径，尝试在 CORE_DIR 和 PRIVILEGED_DIR 中查找
            if not script_path.is_absolute():
                # 先尝试 CORE_DIR
                core_path = CORE_DIR / script_path
                if core_path.exists():
                    script_path = core_path
                else:
                    # 再尝试 PRIVILEGED_DIR
                    priv_path = PRIVILEGED_DIR / script_path
                    if priv_path.exists():
                        script_path = priv_path
                    else:
                        logger.warning(f"模块文件不存在: {script_path}")
                        return None

            if not script_path.exists():
                logger.warning(f"模块文件不存在: {script_path}")
                return None

            spec = importlib.util.spec_from_file_location(
                module_name,
                script_path
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._loaded_modules[module_name] = module
                logger.debug(f"模块加载成功: {module_name}")
                return module
        except Exception as e:
            logger.warning(f"模块加载失败 {module_name}: {e}")
            return None

        return None

    def get_modules_by_layer(self, layer: int) -> List[str]:
        """获取指定层的所有模块"""
        if layer in self._layer_cache:
            return self._layer_cache[layer]

        modules = [
            name for name, info in self.modules.items()
            if info.layer == layer
        ]
        self._layer_cache[layer] = modules
        return modules

    def detect_module(self, user_message: str) -> List[Tuple[str, float]]:
        """检测用户意图，返回匹配的模块列表"""
        matches = []
        message_lower = user_message.lower()

        for module_name, module_info in self.modules.items():
            if not module_info.enabled:
                continue

            score = 0.0
            for trigger in module_info.triggers:
                if trigger in message_lower:
                    score += 0.3

            if score > 0:
                matches.append((module_name, min(score, 1.0)))

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    def get_integrated_workflow(self, scenario: str) -> List[Tuple[str, str]]:
        """获取集成工作流"""
        if not self._workflows:
            self._workflows = self._build_workflows()
        return self._workflows.get(scenario, [])

    def _build_workflows(self) -> Dict[str, List[Tuple[str, str]]]:
        """构建工作流字典"""
        return {
            # 原有工作流
            "learn_from_mistake": [
                ("reflection_nl", "处理用户纠正"),
                ("memory_reflector", "记录错误"),
                ("synapse_network", "创建记忆神经元"),
                ("emotion_memory", "计算情感权重"),
                ("adaptive_memory", "优化参数"),
            ],
            "recall_memory": [
                ("hallucination_guard", "验证记忆有效性"),
                ("synapse_network", "激活传播"),
                ("emotion_memory", "获取高优先级记忆"),
            ],
            "self_improve": [
                ("memory_reflector", "分析错误模式"),
                ("adaptive_memory", "优化系统参数"),
                ("skill_coordinator", "推荐思考技能"),
            ],
            "creative_thinking": [
                ("skill_coordinator", "调用创新工作流"),
                ("synapse_network", "联想记忆"),
                ("emotion_memory", "情感驱动"),
            ],
            "safe_generation": [
                ("hallucination_guard", "生成前检查"),
                ("hallucination_guard", "输出验证"),
                ("hallucination_guard", "不确定性表达"),
            ],

            # 新增工作流 - 检索增强
            "enhanced_recall": [
                ("crag_pipeline", "CRAG 纠错检索"),
                ("hybrid_search", "混合检索"),
                ("rag_cache", "RAG 缓存"),
                ("proposition_retrieval", "命题检索"),
            ],
            "intelligent_search": [
                ("hybrid_search", "Dense+Sparse 混合检索"),
                ("rag_failure_detector", "失败检测"),
                ("rag_optimizer", "检索优化"),
            ],

            # 新增工作流 - LLM 优化
            "fast_generation": [
                ("speculative_decoder", "投机解码"),
                ("streaming_llm", "流式生成"),
                ("model_router", "模型路由"),
            ],
            "optimized_inference": [
                ("model_router", "选择最优模型"),
                ("speculative_decoder", "投机解码加速"),
                ("model_performance", "性能监控"),
            ],


            # 新增工作流 - 缓存优化
            "smart_caching": [
                ("semantic_cache", "语义缓存"),
                ("unified_cache", "统一缓存"),
                ("cache_allocator", "缓存分配"),
            ],

            # 新增工作流 - 硬件优化
            "hardware_acceleration": [
                ("hardware_optimize", "硬件检测"),
                ("numa_optimizer", "NUMA 优化"),
                ("mkl_accelerator", "MKL 加速"),
            ],

            # 新增工作流 - 系统可靠性
            "self_healing": [
                ("failover", "故障检测"),
                ("full_recovery", "自动恢复"),
                ("safety_alignment", "安全检查"),
            ],

            # 新增工作流 - 会话管理
            "long_conversation": [
                ("conversation", "会话管理"),
                ("context_compressor", "上下文压缩"),
                ("rag_cache", "知识缓存"),
            ],

            # 新增工作流 - Persona 更新
            "auto_learning": [
                ("auto_update_persona", "自动学习"),
                ("smart_memory_update", "智能更新"),
                ("memory_reflector", "反思改进"),
            ],

            # 新增工作流 - MemGPT 三级内存
            "memgpt_recall": [
                ("memgpt_memory", "Core Memory 检索"),
                ("memgpt_memory", "Working Memory 压缩"),
                ("memgpt_memory", "Archival Memory 向量检索"),
            ],
            "memgpt_store": [
                ("memgpt_memory", "重要性评估"),
                ("memgpt_memory", "自动分级存储"),
                ("memgpt_memory", "上下文更新"),
            ],

            # 新增工作流 - Generative Agents 反思
            "reflection_cycle": [
                ("generative_agents", "记忆流收集"),
                ("generative_agents", "反思触发检测"),
                ("generative_agents", "洞察生成"),
                ("generative_agents", "行动计划制定"),
            ],
            "importance_recall": [
                ("generative_agents", "检索公式计算"),
                ("generative_agents", "重要性加权"),
                ("generative_agents", "时效性衰减"),
            ],

            # 新增工作流 - Self-RAG
            "self_rag_query": [
                ("self_rag", "IsREL 检索必要性预测"),
                ("self_rag", "检索执行"),
                ("self_rag", "IsSUP 检索支持度评估"),
                ("self_rag", "知识精炼"),
                ("self_rag", "IsUSE 生成可靠性验证"),
            ],
            "adaptive_retrieval": [
                ("self_rag", "查询分析"),
                ("self_rag", "检索策略选择"),
                ("self_rag", "迭代优化"),
            ],

            # 新增工作流 - Knowledge Graph GNN
            "gnn_reasoning": [
                ("knowledge_graph_gnn", "实体识别"),
                ("knowledge_graph_gnn", "图构建"),
                ("knowledge_graph_gnn", "GNN 嵌入学习"),
                ("knowledge_graph_gnn", "关系预测"),
            ],
            "kg_completion": [
                ("knowledge_graph_gnn", "缺失关系检测"),
                ("knowledge_graph_gnn", "链接预测"),
                ("knowledge_graph_gnn", "图谱补全"),
            ],

            # ==================== 跨模块协同工作流 ====================
            # 完整认知闭环：检索 → 推理 → 反思 → 存储
            "cognitive_loop": [
                ("self_rag", "IsREL 判断是否需要检索"),
                ("memgpt_memory", "Core Memory 快速检索"),
                ("memgpt_memory", "Archival Memory 向量检索"),
                ("knowledge_graph_gnn", "知识图谱实体查询"),
                ("knowledge_graph_gnn", "GNN 关系推理"),
                ("self_rag", "IsSUP 评估检索结果"),
                ("self_rag", "知识精炼与生成"),
                ("self_rag", "IsUSE 验证生成可靠性"),
                ("memgpt_memory", "新记忆分级存储"),
            ],

            # 智能问答流程
            "smart_qa": [
                ("self_rag", "查询分析与检索决策"),
                ("memgpt_memory", "三级内存检索"),
                ("knowledge_graph_gnn", "实体关系增强"),
                ("self_rag", "检索结果评估与精炼"),
                ("self_rag", "生成与可靠性验证"),
            ],

            # 深度反思流程
            "deep_reflection": [
                ("generative_agents", "收集近期记忆流"),
                ("generative_agents", "计算重要性分数"),
                ("generative_agents", "触发反思引擎"),
                ("generative_agents", "生成洞察与规划"),
                ("knowledge_graph_gnn", "实体关系分析"),
                ("memgpt_memory", "洞察存入 Core Memory"),
            ],

            # 知识演化流程
            "knowledge_evolution": [
                ("memgpt_memory", "Archival Memory 检索相关记忆"),
                ("knowledge_graph_gnn", "提取实体与关系"),
                ("knowledge_graph_gnn", "GNN 学习新嵌入"),
                ("knowledge_graph_gnn", "链接预测发现隐含关系"),
                ("memgpt_memory", "更新 Core Memory 关键知识"),
                ("generative_agents", "触发反思提取新洞察"),
            ],

            # 记忆巩固流程（睡眠时执行）
            "memory_consolidation": [
                ("memgpt_memory", "Working Memory 压缩"),
                ("generative_agents", "记忆流重要性重评"),
                ("generative_agents", "反思提取长期洞察"),
                ("knowledge_graph_gnn", "构建知识图谱快照"),
                ("memgpt_memory", "Core Memory 驱逐低优先级"),
                ("memgpt_memory", "Archival Memory 整理索引"),
            ],

            # 自我改进流程
            "self_improvement_v2": [
                ("generative_agents", "分析近期错误模式"),
                ("generative_agents", "反思生成改进建议"),
                ("knowledge_graph_gnn", "识别知识盲区"),
                ("self_rag", "检索补充知识"),
                ("memgpt_memory", "存储改进规则到 Core"),
            ],

            # 多跳推理流程
            "multi_hop_reasoning": [
                ("self_rag", "初始查询分析"),
                ("knowledge_graph_gnn", "第一跳实体查询"),
                ("knowledge_graph_gnn", "GNN 邻居聚合"),
                ("knowledge_graph_gnn", "第二跳关系推理"),
                ("self_rag", "多跳结果验证"),
                ("memgpt_memory", "推理链存储"),
            ],

            # ==================== Layer 1.6 集成模块工作流 ====================
            # 规则管理
            "rule_management": [
                ("rules_manager", "加载所有规则文件"),
                ("rules_manager", "规则验证"),
                ("rules_manager", "生成规则摘要"),
            ],
            "rule_sync": [
                ("rules_manager", "检测规则变更"),
                ("rules_manager", "同步规则到协调器"),
                ("rules_manager", "更新触发词"),
            ],

            # 自主任务集成
            "autonomous_execution": [
                ("autonomous_integrator", "检查主动任务"),
                ("autonomous_integrator", "执行任务"),
                ("autonomous_integrator", "更新任务状态"),
                ("heartbeat_task_executor", "记录执行结果"),
            ],

            # 完整集成流程
            "full_integrated_recall": [
                ("crag", "CRAG 纠错检索"),
                ("retrieval_hub", "混合检索"),
                ("skill_coordinator", "思考技能路由"),
                ("autonomous_integrator", "自主任务检查"),
                ("xiaoyi_memory", "向量记忆优化"),
            ],

            # 统一入口
            "smart_recall_flow": [
                ("xiaoyi_memory_v2", "smart_recall 入口"),
                ("xiaoyi_claw_api", "enhanced_recall 检索"),
                ("memgpt_memory", "三级内存补充"),
                ("self_rag", "结果验证"),
            ],
            "smart_answer_flow": [
                ("xiaoyi_memory_v2", "smart_answer 入口"),
                ("self_rag", "IsREL 检索决策"),
                ("xiaoyi_claw_api", "smart_answer 知识检索"),
                ("enhanced_hallucination_guard", "多源验证"),
                ("self_rag", "IsUSE 可靠性验证"),
            ],

            # 优化集成
            "adaptive_optimization": [
                ("optimization_integration", "防幻觉参数自适应"),
                ("optimization_integration", "CRAG 阈值动态调整"),
                ("optimization_integration", "LTP/LTD 自适应"),
                ("optimization_integration", "RRF 融合权重调整"),
                ("optimization_integration", "思考技能智能触发"),
            ],

            # 心跳任务
            "heartbeat_execution": [
                ("heartbeat_task_executor", "主动任务检查"),
                ("heartbeat_task_executor", "记忆维护"),
                ("heartbeat_task_executor", "健康检查"),
                ("heartbeat_task_executor", "技能更新检查"),
                ("heartbeat_task_executor", "备份检查"),
            ],

            # 增强防幻觉
            "enhanced_verification": [
                ("enhanced_hallucination_guard", "多源交叉验证"),
                ("enhanced_hallucination_guard", "思考能力增强"),
                ("enhanced_hallucination_guard", "渐进式验证"),
                ("enhanced_hallucination_guard", "共识判断"),
            ],

            # ==================== Layer 2-3 检索增强工作流 ====================
            "advanced_retrieval": [
                ("late_chunking", "上下文感知分块"),
                ("multiresolution_search", "多分辨率检索"),
                ("distributed_search", "分布式并行检索"),
                ("cross_lingual", "跨语言检索"),
                ("multimodal_search", "多模态检索"),
            ],
            "vector_optimization": [
                ("ann_selector", "ANN 算法选择"),
                ("opq_quantization", "OPQ 量化压缩"),
                ("sparse_anns", "稀疏向量索引"),
                ("vector_quantization", "INT8/FP16 量化"),
                ("vector_api", "统一向量操作"),
            ],

            # ==================== Layer 4-5 LLM 与缓存工作流 ====================
            "llm_optimization": [
                ("llm_client", "统一 LLM 调用"),
                ("llm_streaming", "异步流式生成"),
                ("model_router", "模型路由选择"),
                ("model_performance", "性能监控"),
            ],
            "cache_optimization": [
                ("cache_aware_scheduler", "NUMA 感知调度"),
                ("computational_storage", "KV Cache 管理"),
                ("approximate_cache", "LSH 近似查找"),
                ("unified_cache", "统一缓存管理"),
            ],

            # ==================== Layer 6 硬件优化工作流 ====================
            "hardware_full_optimization": [
                ("hardware_optimize", "硬件检测"),
                ("gpu_optimizer", "GPU 加速"),
                ("numa_optimizer", "NUMA 优化"),
                ("mkl_accelerator", "MKL 加速"),
                ("huge_page_manager", "大页内存管理"),
                ("io_optimizer", "IO 优化"),
            ],
            "advanced_hardware": [
                ("cxl_optimizer", "CXL 内存池管理"),
                ("fma_accelerator", "FMA 指令加速"),
                ("kunpeng_optimizer", "鲲鹏架构优化"),
                ("realtime_scheduler", "实时调度"),
                ("irq_isolator", "IRQ 隔离"),
                ("power_manager", "电源管理"),
                ("zram_detector", "ZRAM 检测"),
            ],

            # ==================== Layer 7-8 协调与可靠性工作流 ====================
            "module_coordination": [
                ("module_coordinator", "进程管理"),
                ("resource_orchestrator", "资源编排"),
                ("tools_registry", "工具注册"),
                ("auto_tuner", "自动调优"),
            ],
            "system_reliability": [
                ("failover", "故障检测"),
                ("full_recovery", "自动恢复"),
                ("sandbox_manager", "沙箱隔离"),
                ("safety_alignment", "安全对齐"),
                ("exceptions", "异常处理"),
            ],

            # ==================== Layer 9-10 会话与 Persona 工作流 ====================
            "session_management": [
                ("conversation", "会话管理"),
                ("context_compressor", "上下文压缩"),
                ("acp_server", "ACP 通信"),
            ],
            "dag_context_assembly": [
                ("dag_context_manager", "DAG 节点加载"),
                ("dag_context_manager", "优先级装配"),
                ("dag_context_manager", "人格保护"),
                ("dag_context_manager", "可逆回溯"),
            ],
            "dag_context_summarize": [
                ("dag_context_manager", "DAG 增量摘要生成"),
                ("dag_context_manager", "语义缓存查询"),
                ("dag_context_manager", "突触网络反馈"),
                ("dag_context_manager", "MKL 加速检测"),
            ],
            "persona_management": [
                ("auto_update_persona", "自动学习"),
                ("update_persona", "手动更新"),
                ("update_l3_profile", "长期记忆更新"),
                ("smart_memory_update", "智能更新"),
            ],

            # ==================== 补充工作流 - 覆盖剩余模块 ====================
            # Layer 1 记忆核心补充
            "memory_core_full": [
                ("memory_reflector", "记忆反思"),
                ("synapse_network", "突触网络激活"),
                ("emotion_memory", "情感记忆检索"),
                ("adaptive_memory", "自适应优化"),
                ("hallucination_guard", "防幻觉检查"),
            ],

            # Layer 2 RAG 完整流程
            "rag_full_pipeline": [
                ("crag_pipeline", "CRAG 纠错检索"),
                ("hybrid_search", "混合检索"),
                ("proposition_retrieval", "命题检索"),
                ("rag_cache", "RAG 缓存"),
                ("rag_failure_detector", "失败检测"),
                ("rag_optimizer", "检索优化"),
            ],

            # Layer 4-5 性能优化补充
            "performance_boost": [
                ("speculative_decoder", "投机解码"),
                ("streaming_llm", "流式生成"),
                ("cache_allocator", "缓存分配"),
                ("semantic_cache", "语义缓存"),
            ],

            # Layer 11 思考技能
            "thinking_skills": [
                ("skill_coordinator", "技能协调"),
                ("skill_coordinator", "第一性原理"),
                ("skill_coordinator", "系统思维"),
                ("skill_coordinator", "批判性思维"),
            ],

            # ==================== 补充工作流 - 覆盖剩余模块 ====================
            # NLP 处理
            "nlp_processing": [
                ("nlp_processor", "中文分词"),
                ("nlp_processor", "命名实体识别"),
                ("nlp_processor", "关键词提取"),
                ("nlp_integration", "记忆关键词提取"),
                ("nlp_integration", "检索分词优化"),
            ],

            # 知识图谱 GNN
            "knowledge_graph_full": [
                ("graph_constructor", "图构建"),
                ("graphsage_layer", "GraphSAGE 聚合"),
                ("gat_layer", "GAT 注意力"),
                ("relation_predictor", "关系预测"),
                ("knowledge_augmentor", "知识增强"),
            ],

            # Self-RAG 完整流程
            "self_rag_full": [
                ("isrel_predictor", "IsREL 检索必要性"),
                ("retrieval_evaluator", "检索评估"),
                ("issup_predictor", "IsSUP 检索支持度"),
                ("knowledge_refiner", "知识精炼"),
                ("isuse_predictor", "IsUSE 生成可靠性"),
            ],

            # Generative Agents 完整流程
            "generative_agents_full": [
                ("memory_stream", "记忆流管理"),
                ("retrieval_formula", "检索公式计算"),
                ("reflection_engine", "反思引擎"),
                ("planning_engine", "规划引擎"),
            ],

            # 记忆系统完整流程
            "memory_full": [
                ("memory_functions", "记忆函数"),
                ("memory_bank", "记忆库"),
                ("memory_synapse_network", "突触网络"),
                ("importance_scorer", "重要性评分"),
                ("smart_forgetter", "智能遗忘"),
            ],

            # 多模态记忆
            "multimodal_memory_flow": [
                ("multimodal_memory", "多模态记忆存储"),
                ("multimodal_search", "多模态检索"),
                ("visual_generation", "视觉生成"),
            ],

            # 反思系统
            "reflection_system": [
                ("reflection_heartbeat", "心跳反思"),
                ("reflection_chat", "对话反思"),
                ("auto_learner", "自动学习"),
                ("intelligent_thinking_trigger", "智能思考触发"),
            ],

            # Persona 更新
            "persona_update": [
                ("update_persona", "Persona 更新"),
                ("update_l3_profile", "L3 Profile 更新"),
                ("smart_memory_update", "智能记忆更新"),
                ("auto_update_persona", "自动更新 Persona"),
            ],

            # 向量存储
            "vector_store_full": [
                ("unified_vector_store", "统一向量存储"),
                ("sqlite_vec", "SQLite 向量扩展"),
                ("sqlite_ext", "SQLite 扩展"),
                ("quantization", "向量量化"),
            ],

            # 系统可靠性
            "resilience_full": [
                ("resilience_system", "弹性系统"),
                ("full_recovery", "完整恢复"),
                ("task_memory_bridge", "任务记忆桥接"),
                ("brain_memory_sync", "Brain 记忆同步"),
            ],

            # 防幻觉集成
            "hallucination_full": [
                ("hallucination_integration", "防幻觉集成"),
                ("dynamic_crag_threshold", "CRAG 动态阈值"),
                ("retrieval_eval", "检索评估"),
            ],

            # 异步与平台
            "async_platform": [
                ("async_ops", "异步操作"),
                ("platform_adapter", "平台适配"),
                ("logging_config", "日志配置"),
                ("dep_checker", "依赖检查"),
            ],

            # 统一入口
            "unified_entry": [
                ("xiaoyi_memory", "统一记忆入口"),
                ("xiaoyi_claw_api", "统一 API"),
                ("unified_coordinator", "统一协调器"),
            ],

            # 混合检索
            "hybrid_memory_search_flow": [
                ("hybrid_memory_search", "混合记忆检索"),
                ("proposition_retriever", "命题检索"),

            ],

            # 大页内存管理
            "hugepage_optimization": [
                ("hugepage_manager", "大页内存管理"),
                ("progressive_setup", "渐进式设置"),
            ],
        }

        return workflows.get(scenario, [])

    def execute_workflow(
        self,
        scenario: str,
        initial_input: Any = None
    ) -> Dict[str, Any]:
        """
        执行工作流
        
        Args:
            scenario: 场景名称
            initial_input: 初始输入
        
        Returns:
            执行结果
        """
        workflow = self.get_integrated_workflow(scenario)
        if not workflow:
            return {"error": f"未找到场景: {scenario}"}

        results = []
        current_input = initial_input

        for module_name, action in workflow:
            module = self._load_module(module_name)
            if not module:
                results.append({
                    "module": module_name,
                    "action": action,
                    "status": "failed",
                    "error": "模块加载失败"
                })
                continue

            try:
                # 将中文 action 映射为实际方法调用
                action_method_map = {
                    # autonomous_integrator 动作
                    "检查主动任务": ("get_next_proactive_task", False),
                    "执行任务": ("run_heartbeat_tasks", False),
                    "更新任务状态": ("get_autonomous_status", False),
                    "记录执行结果": ("run_heartbeat_tasks", False),
                    # heartbeat_task_executor 动作
                    "主动任务检查": ("_check_proactive_tasks", False),
                    "记忆维护": ("_optimize_memory", False),
                    "健康检查": ("_health_check", False),
                    "技能更新检查": ("_check_skill_updates", False),
                    "备份检查": ("_check_backup_needed", False),
                    # heartbeat 执行器
                    "心跳执行器": ("execute_heartbeat", True),
                    # autonomous 集成
                    "自主任务": ("get_autonomous_status", False),
                    # rules_manager & hallucination_guard
                    "规则管理": ("get_rules_summary", False),
                    "增强防幻觉": ("verify_with_cross_validation", False),
                }

                result_value = None
                if module_name in action_method_map or action in [v[0] for v in action_method_map.values()]:
                    # 优先通过 action 查找
                    method_name = action_method_map.get(action, (None,))[0]
                    if not method_name:
                        # 通过方法名反向查找
                        for act, (mname, _) in action_method_map.items():
                            if act == action:
                                method_name = mname
                                break
                    if method_name and hasattr(module, method_name):
                        method = getattr(module, method_name)
                        if action_method_map.get(action, (None, False))[1]:
                            # 需要传入 current_input 的方法
                            result_value = method(current_input) if callable(method) else method
                        else:
                            result_value = method() if callable(method) else method
                        if hasattr(result_value, 'results'):
                            result_value = result_value.results

                results.append({
                    "module": module_name,
                    "action": action,
                    "status": "success" if result_value is None or isinstance(result_value, (dict, list, str, int, float, bool, type(None))) else "success",
                    "result": str(result_value)[:200] if result_value is not None else None,
                    "input": str(current_input)[:100] if current_input is not None else None
                })
            except Exception as e:
                results.append({
                    "module": module_name,
                    "action": action,
                    "status": "failed",
                    "error": str(e)
                })

        return {
            "scenario": scenario,
            "results": results,
            "total_steps": len(workflow),
            "success_count": sum(1 for r in results if r["status"] == "success")
        }

    def cognitive_recall(self, query: str, context: str = None) -> Dict[str, Any]:
        """
        认知检索 - 跨模块协同的高级检索
        
        整合 Self-RAG + MemGPT + Knowledge Graph GNN
        
        Args:
            query: 用户查询
            context: 可选上下文
            
        Returns:
            检索结果
        """
        results = {
            "query": query,
            "steps": [],
            "final_answer": None,
            "confidence": 0.0
        }

        # Step 1: Self-RAG 判断是否需要检索
        self_rag = self._load_module("self_rag")
        if self_rag:
            try:
                from self_rag import IsRELPredictor
                predictor = IsRELPredictor()
                decision = predictor.predict(query, context)
                results["steps"].append({
                    "step": "isrel_prediction",
                    "should_retrieve": decision.should_retrieve,
                    "confidence": decision.confidence
                })
            except Exception as e:
                results["steps"].append({
                    "step": "isrel_prediction",
                    "error": str(e)
                })

        # Step 2: MemGPT 三级内存检索
        memgpt = self._load_module("memgpt_memory")
        if memgpt:
            try:
                from memgpt_memory import MemGPTMemory
                memory = MemGPTMemory()
                recalled = memory.recall(query, top_k=5)
                results["steps"].append({
                    "step": "memgpt_recall",
                    "memories_found": len(recalled),
                    "sources": [m.memory_type.value for m in recalled]
                })
            except Exception as e:
                results["steps"].append({
                    "step": "memgpt_recall",
                    "error": str(e)
                })

        # Step 3: Knowledge Graph GNN 实体查询
        kg = self._load_module("knowledge_graph_gnn")
        if kg:
            try:
                from knowledge_graph_gnn import KnowledgeGraphGNN
                graph = KnowledgeGraphGNN()
                entities = graph.query_graph(query, top_k=5)
                results["steps"].append({
                    "step": "kg_query",
                    "entities_found": len(entities),
                    "entity_names": [e[0].name for e in entities if hasattr(e[0], 'name')]
                })
            except Exception as e:
                results["steps"].append({
                    "step": "kg_query",
                    "error": str(e)
                })

        return results

    def deep_reflect(self, recent_hours: int = 24) -> Dict[str, Any]:
        """
        深度反思 - 跨模块协同的反思流程
        
        整合 Generative Agents + Knowledge Graph GNN + MemGPT
        
        Args:
            recent_hours: 分析最近多少小时的记忆
            
        Returns:
            反思结果
        """
        results = {
            "analysis_period": f"最近 {recent_hours} 小时",
            "steps": [],
            "insights": []
        }

        # Step 1: 收集记忆流
        ga = self._load_module("generative_agents")
        if ga:
            try:
                from memory_stream import MemoryStream
                from reflection_engine import ReflectionEngine

                stream = MemoryStream()
                engine = ReflectionEngine(stream)

                # 检查是否应该反思
                should, trigger = engine.should_reflect()
                results["steps"].append({
                    "step": "reflection_check",
                    "should_reflect": should,
                    "trigger": trigger.value if hasattr(trigger, 'value') else str(trigger)
                })

                # 执行反思
                if should:
                    insights = engine.reflect()
                    results["insights"] = [i.content for i in insights]
                    results["steps"].append({
                        "step": "reflection_executed",
                        "insights_generated": len(insights)
                    })
            except Exception as e:
                results["steps"].append({
                    "step": "reflection",
                    "error": str(e)
                })

        return results

    def get_cross_module_workflows(self) -> Dict[str, str]:
        """获取跨模块协同工作流列表"""
        return {
            # Layer 1.5 跨模块工作流
            "cognitive_loop": "完整认知闭环：检索 → 推理 → 反思 → 存储",
            "smart_qa": "智能问答流程",
            "deep_reflection": "深度反思流程",
            "knowledge_evolution": "知识演化流程",
            "memory_consolidation": "记忆巩固流程（睡眠时执行）",
            "self_improvement_v2": "自我改进流程",
            "multi_hop_reasoning": "多跳推理流程",

            # Layer 1.6 集成模块工作流
            "rule_management": "规则管理：加载/验证/摘要",
            "rule_sync": "规则同步：检测变更/同步/更新触发词",
            "autonomous_execution": "自主任务执行：检查/执行/更新/记录",
            "full_integrated_recall": "完整集成检索：CRAG/混合/思考/自主/向量",
            "smart_recall_flow": "智能召回流程：统一入口/完整集成/三级内存/验证",
            "smart_answer_flow": "智能回答流程：统一入口/检索决策/知识检索/多源验证",
            "adaptive_optimization": "自适应优化：防幻觉/CRAG/LTP-LTD/RRF/思考触发",
            "heartbeat_execution": "心跳执行：主动任务/记忆维护/健康检查/技能更新/备份",
            "enhanced_verification": "增强验证：多源交叉验证/思考增强/渐进式验证",

            # Layer 2-3 检索增强工作流
            "advanced_retrieval": "高级检索：分块/多分辨率/分布式/跨语言/多模态",
            "vector_optimization": "向量优化：ANN选择/OPQ量化/稀疏索引/INT8量化",

            # Layer 4-5 LLM 与缓存工作流
            "llm_optimization": "LLM优化：统一调用/流式生成/模型路由/性能监控",
            "cache_optimization": "缓存优化：NUMA调度/KV管理/LSH近似/统一缓存",

            # Layer 6 硬件优化工作流
            "hardware_full_optimization": "硬件全优化：GPU/NUMA/MKL/大页/IO",
            "advanced_hardware": "高级硬件：CXL/FMA/鲲鹏/实时调度/IRQ/电源",

            # Layer 7-8 协调与可靠性工作流
            "module_coordination": "模块协调：进程管理/资源编排/工具注册/自动调优",
            "system_reliability": "系统可靠性：故障检测/自动恢复/沙箱/安全对齐",

            # Layer 9-10 会话与 Persona 工作流
            "session_management": "会话管理：会话/上下文压缩/ACP通信",
            "persona_management": "Persona管理：自动学习/手动更新/长期记忆/智能更新",

            # 补充工作流
            "memory_core_full": "记忆核心完整：反思/突触/情感/自适应/防幻觉",
            "rag_full_pipeline": "RAG完整流程：CRAG/混合/命题/缓存/检测/优化",
            "performance_boost": "性能提升：投机解码/流式生成/缓存分配/语义缓存",
            "thinking_skills": "思考技能：技能协调/第一性原理/系统思维/批判性思维",
        }

    def get_module_status(self) -> Dict[str, Any]:
        """获取所有模块状态"""
        status = {}

        for module_name, module_info in self.modules.items():
            status[module_name] = {
                "name": module_info.name,
                "type": module_info.module_type.value,
                "layer": module_info.layer,
                "description": module_info.description,
                "loaded": module_name in self._loaded_modules,
                "enabled": module_info.enabled,
                "dependencies": module_info.dependencies
            }

        return status

    def get_layer_summary(self) -> Dict[int, Dict]:
        """获取各层模块摘要"""
        summary = {}

        for layer in range(1, 12):
            modules = self.get_modules_by_layer(layer)
            loaded = sum(1 for m in modules if m in self._loaded_modules)

            summary[layer] = {
                "total": len(modules),
                "loaded": loaded,
                "modules": modules
            }

        return summary


# CLI 接口
def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="统一协调器 V2")
    parser.add_argument("command", choices=["status", "layers", "workflow", "detect"])
    parser.add_argument("--scenario", help="场景名称")
    parser.add_argument("--message", help="用户消息")

    args = parser.parse_args()

    coordinator = UnifiedCoordinator()

    if args.command == "status":
        status = coordinator.get_module_status()
        print(f"模块状态 (共 {len(status)} 个):")

        # 按层分组显示
        layers = {}
        for name, info in status.items():
            layer = info["layer"]
            if layer not in layers:
                layers[layer] = []
            layers[layer].append((name, info))

        for layer in sorted(layers.keys()):
            print(f"\nLayer {layer}:")
            for name, info in layers[layer]:
                loaded = "✅" if info["loaded"] else "⏸️"
                enabled = "🟢" if info["enabled"] else "🔴"
                print(f"  {loaded}{enabled} {name}")

    elif args.command == "layers":
        summary = coordinator.get_layer_summary()
        print("各层模块摘要:")
        for layer, info in summary.items():
            print(f"\nLayer {layer}: {info['loaded']}/{info['total']} 已加载")

    elif args.command == "workflow":
        if not args.scenario:
            print("错误: 需要提供 --scenario")
            return

        workflow = coordinator.get_integrated_workflow(args.scenario)
        if workflow:
            print(f"工作流: {args.scenario}")
            for i, (module, action) in enumerate(workflow, 1):
                print(f"  {i}. {module}: {action}")
        else:
            print(f"未找到场景: {args.scenario}")

    elif args.command == "detect":
        if not args.message:
            print("错误: 需要提供 --message")
            return

        matches = coordinator.detect_module(args.message)
        print("检测到的模块:")
        for name, score in matches:
            info = coordinator.modules[name]
            print(f"  - {name} (Layer {info.layer}): {score:.2f}")


if __name__ == "__main__":
    main()


# ==================== 弹性系统集成（从 v1 合并）====================

def get_resilience_status() -> Dict:
    """
    获取弹性系统状态
    
    Returns:
        Dict: 弹性系统健康报告
    """
    try:
        from resilience_system import get_resilience_system
        system = get_resilience_system()
        return system.get_health_report()
    except Exception as e:
        return {
            'error': str(e),
            'degradation_level': 4,
            'degradation_desc': '弹性系统不可用'
        }

def get_available_components() -> List[str]:
    """
    获取当前可用的组件列表
    
    Returns:
        List[str]: 可用组件ID列表
    """
    try:
        from resilience_system import get_resilience_system
        system = get_resilience_system()
        available = []
        for comp_id, comp in system.components.items():
            if comp.status.value == 'healthy':
                available.append(comp_id)
        return available
    except:
        return []

def safe_call(component_id: str, method: str, *args, **kwargs) -> Any:
    """
    安全调用组件方法（带故障转移）
    
    Args:
        component_id: 组件ID
        method: 方法名
        *args, **kwargs: 方法参数
        
    Returns:
        Any: 调用结果，失败返回None
    """
    try:
        from resilience_system import get_resilience_system
        system = get_resilience_system()

        # 获取组件实例
        instance = system.get_instance(component_id)
        if instance is None:
            logger.warning(f"组件 {component_id} 不可用")
            return None

        # 调用方法
        method_func = getattr(instance, method)
        return method_func(*args, **kwargs)

    except Exception as e:
        logger.error(f"安全调用失败: {component_id}.{method} - {e}")
        return None

def get_coordinator_status() -> Dict:
    """
    获取协调器完整状态
    
    Returns:
        Dict: 协调器状态报告
    """
    return {
        "modules": {
            "total": len(MODULE_REGISTRY),
            "by_type": {
                mt.value: sum(1 for m in MODULE_REGISTRY.values() if m.module_type == mt)
                for mt in ModuleType
            }
        },
        "workflows": {
            "total": len(INTEGRATED_WORKFLOWS) if 'INTEGRATED_WORKFLOWS' in dir() else 0,
            "names": list(INTEGRATED_WORKFLOWS.keys()) if 'INTEGRATED_WORKFLOWS' in dir() else []
        },
        "resilience": get_resilience_status()
    }


# ==================== P1: 扩展模块注册 ====================

EXTENDED_MODULES_P1 = {
    # 投机解码
    "speculative_decoder": ModuleInfo(
        name="speculative_decoder",
        module_type=ModuleType.SPECULATIVE_DECODER,
        description="投机解码器 - 检索型",
        triggers=["草稿生成", "验证解码"],
        script_path="speculative_decoder.py"
    ),
    # 缓存层
    "rag_cache": ModuleInfo(
        name="rag_cache",
        module_type=ModuleType.RAG_CACHE,
        description="RAG缓存 - 结果缓存",
        triggers=["缓存结果", "加速检索"],
        script_path="rag_cache.py"
    ),
    "semantic_cache": ModuleInfo(
        name="semantic_cache",
        module_type=ModuleType.RAG_CACHE,
        description="语义缓存 - 相似查询复用",
        triggers=["语义缓存", "相似查询"],
        script_path="semantic_cache.py"
    ),
    # 对话管理
    "conversation": ModuleInfo(
        name="conversation",
        module_type=ModuleType.STREAMING_LLM,
        description="对话管理 - 上下文追踪",
        triggers=["对话历史", "上下文"],
        script_path="conversation.py"
    ),
    "context_compressor": ModuleInfo(
        name="context_compressor",
        module_type=ModuleType.STREAMING_LLM,
        description="上下文压缩 - 长对话处理",
        triggers=["压缩上下文", "长对话"],
        script_path="context_compressor.py"
    ),
    # 记忆扩展
    "memgpt_memory": ModuleInfo(
        name="memgpt_memory",
        module_type=ModuleType.MEMGPT_MEMORY,
        description="MemGPT记忆 - 分层记忆管理",
        triggers=["分层记忆", "核心记忆"],
        script_path="memgpt_memory.py"
    ),
    "brain_sync": ModuleInfo(
        name="brain_sync",
        module_type=ModuleType.MEMORY_REFLECTION,
        description="知识库同步 - 2nd-brain集成",
        triggers=["同步知识库", "brain"],
        script_path="brain_memory_sync.py"
    ),
    # CRAG增强
    "crag_pipeline": ModuleInfo(
        name="crag_pipeline",
        module_type=ModuleType.CRAG_PIPELINE,
        description="CRAG流水线 - 完整流程",
        triggers=["CRAG流程", "纠错流水线"],
        script_path="crag_pipeline.py"
    ),
    # 新论文模块（2026-06-14）
    "engram_memory": ModuleInfo(
        name="engram_memory",
        module_type=ModuleType.ENGRAM_MEMORY,
        description="Engram 条件记忆 - N-gram 哈希查找 O(1) 条件记忆",
        triggers=["条件记忆", "查找记忆", "Engram"],
        script_path="engram_memory.py",
        layer=1
    ),
    "kan_network": ModuleInfo(
        name="kan_network",
        module_type=ModuleType.KAN_NETWORK,
        description="KAN 网络 - 可学习 B-spline 激活函数，替代 MLP",
        triggers=["KAN", "样条网络", "可学习激活"],
        script_path="kan_network.py",
        layer=4
    ),
    "neural_ode": ModuleInfo(
        name="neural_ode",
        module_type=ModuleType.NEURAL_ODE,
        description="Neural ODE - 连续深度模型，ODE 求解器替代离散层",
        triggers=["Neural ODE", "连续深度", "ODE求解"],
        script_path="neural_ode.py",
        layer=4
    ),
    "ltc_se_framework": ModuleInfo(
        name="ltc_se_framework",
        module_type=ModuleType.LIQUID_FRAMEWORK,
        description="LTC-SE 统一框架 - LTC/CfC/LIF/CTRNN/GRU-ODE/NeuralODE 统一接口",
        triggers=["液体框架", "LTC-SE", "连续时间单元"],
        script_path="ltc_se_framework.py",
        layer=4
    ),
    # 第二批论文模块（2026-06-14）
    "liquid_weight": ModuleInfo(
        name="liquid_weight",
        module_type=ModuleType.LIQUID_WEIGHT,
        description="P19: Liquid Weight 独立模块 - 液态权重生成、融合、液态+静态混合",
        triggers=["液态权重", "LiquidWeight", "权重生成"],
        script_path="liquid_weight.py",
        layer=1
    ),
    "dag_liquid_fusion": ModuleInfo(
        name="dag_liquid_fusion",
        module_type=ModuleType.DAG_LIQUID,
        description="P21: DAG + Liquid 融合 - LTC 时间常数驱动的 DAG 上下文压缩策略",
        triggers=["DAG液态", "液态压缩", "上下文压缩"],
        script_path="dag_liquid_fusion.py",
        layer=9
    ),
}

# ==================== P2: 发现遗漏模块 ====================

EXTENDED_MODULES_P2 = {
    # 智能思考触发
    "intelligent_thinking_trigger": ModuleInfo(
        name="intelligent_thinking_trigger",
        module_type=ModuleType.INTELLIGENT_THINKING,
        description="智能思考技能触发 - 意图检测、技能选择、思考路由",
        triggers=["思考", "分析", "反思", "推理"],
        script_path="intelligent_thinking_trigger.py",
        layer=1
    ),
    # 弹性系统
    "resilience_system": ModuleInfo(
        name="resilience_system",
        module_type=ModuleType.RESILIENCE_SYSTEM,
        description="弹性系统 - 故障检测、自动恢复、降级策略、熔断保护",
        triggers=["故障", "恢复", "熔断", "降级", "弹性"],
        script_path="resilience_system.py",
        layer=8
    ),
    # Embedding 增强
    "embedding_enhance": ModuleInfo(
        name="embedding_enhance",
        module_type=ModuleType.EMBEDDING_ENHANCE,
        description="Embedding 增强 - 查询扩展、加权融合、混合编码",
        triggers=["embed增强", "查询扩展", "混合编码"],
        script_path="embedding_enhance.py",
        layer=3
    ),
    # 性能补丁
    "performance_patch": ModuleInfo(
        name="performance_patch",
        module_type=ModuleType.PERFORMANCE_PATCH,
        description="性能优化补丁 - 内存优化、线程优化、缓存预热、批量处理",
        triggers=["性能优化", "补丁", "加速"],
        script_path="performance_patch.py",
        layer=6
    ),
    # 检索公式
    "retrieval_formula": ModuleInfo(
        name="retrieval_formula",
        module_type=ModuleType.RETRIEVAL_FORMULA,
        description="检索公式模块 - 重要性评分、时效性评分、关联性评分、排序融合",
        triggers=["检索公式", "重要性评分", "排序融合"],
        script_path="retrieval_formula.py",
        layer=2
    ),
    # SQLite 向量扩展
    "sqlite_vec": ModuleInfo(
        name="sqlite_vec",
        module_type=ModuleType.SQLITE_VEC,
        description="SQLite 向量扩展 - sqlite-vec 绑定、向量索引、相似搜索",
        triggers=["sqlite向量", "向量索引", "sqlite-vec"],
        script_path="sqlite_vec.py",
        layer=3
    ),
    # 平台适配
    "platform_adapter": ModuleInfo(
        name="platform_adapter",
        module_type=ModuleType.PLATFORM_ADAPTER,
        description="平台适配层 - 系统检测、架构识别、指令集检测、环境适配",
        triggers=["平台检测", "系统信息", "环境适配"],
        script_path="platform_adapter.py",
        layer=8
    ),
    # 异步操作
    "async_ops": ModuleInfo(
        name="async_ops",
        module_type=ModuleType.ASYNC_OPS,
        description="异步操作模块 - 异步检索、批量召回、并发写入、超时控制",
        triggers=["异步", "并发", "批量"],
        script_path="async_ops.py",
        layer=9
    ),
    # 混合记忆搜索
    "hybrid_memory_search": ModuleInfo(
        name="hybrid_memory_search",
        module_type=ModuleType.HYBRID_MEMORY_SEARCH,
        description="混合记忆搜索 - 关键词+向量融合搜索、RRF排序、结果去重",
        triggers=["混合搜索", "融合检索", "RRF"],
        script_path="hybrid_memory_search.py",
        layer=2
    ),
    # 自然语言反思
    "reflection_nl": ModuleInfo(
        name="reflection_nl",
        module_type=ModuleType.REFLECTION_NL,
        description="自然语言反思 - 用户纠正处理、反馈解析、意图理解、自省推理",
        triggers=["纠正", "错误", "反馈", "反思"],
        script_path="reflection_nl.py",
        layer=1
    ),
}

# 合并到主注册表
MODULE_REGISTRY.update(EXTENDED_MODULES_P1)
MODULE_REGISTRY.update(EXTENDED_MODULES_P2)

# ==================== P3: Matt Pocock Skills（工程与效率技能） ====================

# WORKSPACE_ROOT = skills/ 根目录（CORE_DIR = core/，再往上一级是 llm-memory-integration，再上一级是 skills/）
P3_WORKSPACE_ROOT = CORE_DIR.parent.parent

EXTENDED_MODULES_P3 = {
    # 工程类技能（Layer 12）
    "diagnose": ModuleInfo(
        name="diagnose",
        module_type=ModuleType.ENGINEERING_SKILLS,
        description="工程级 Bug 诊断循环。复现→最小化→假设→插桩→修复→回归测试。触发词：debug、诊断、调试、排查、故障、性能回退。",
        triggers=["debug", "诊断", "调试", "排查", "故障", "性能回退"],
        script_path=str(P3_WORKSPACE_ROOT / "diagnose/SKILL.md"),
        layer=12
    ),
    "grill_with_docs": ModuleInfo(
        name="grill_with_docs",
        module_type=ModuleType.ENGINEERING_SKILLS,
        description="需求审问 + 领域语言共建。在动手前让 AI 反复追问需求细节，同步生成 CONTEXT.md 和 ADR。触发词：审审方案、对齐需求、领域语言、需求澄清。",
        triggers=["审审方案", "对齐需求", "领域语言", "需求澄清"],
        script_path=str(P3_WORKSPACE_ROOT / "grill-with-docs/SKILL.md"),
        layer=12
    ),
    "tdd": ModuleInfo(
        name="tdd",
        module_type=ModuleType.ENGINEERING_SKILLS,
        description="测试驱动开发：红-绿-重构循环，垂直切片开发。触发词：TDD、测试驱动、红绿重构、先写测试。",
        triggers=["TDD", "测试驱动", "红绿重构", "先写测试"],
        script_path=str(P3_WORKSPACE_ROOT / "tdd/SKILL.md"),
        layer=12
    ),
    "improve_codebase_architecture": ModuleInfo(
        name="improve_codebase_architecture",
        module_type=ModuleType.ENGINEERING_SKILLS,
        description="代码架构治理。发现模块深度化机会、重构紧耦合、提升可测试性和 AI 导航性。触发词：检查架构、重构、模块深度、代码治理。",
        triggers=["检查架构", "重构", "模块深度", "代码治理"],
        script_path=str(P3_WORKSPACE_ROOT / "improve-codebase-architecture/SKILL.md"),
        layer=12
    ),
    "prototype": ModuleInfo(
        name="prototype",
        module_type=ModuleType.ENGINEERING_SKILLS,
        description="快速原型验证。在正式提交前构建可丢弃的原型，支持终端原型或多种 UI 变体切换。触发词：原型验证、试试这个方案、快速验证。",
        triggers=["原型验证", "试试这个方案", "快速验证"],
        script_path=str(P3_WORKSPACE_ROOT / "prototype/SKILL.md"),
        layer=12
    ),
    "zoom_out": ModuleInfo(
        name="zoom_out",
        module_type=ModuleType.ENGINEERING_SKILLS,
        description="让 AI 退后一步提供全局视角。当你不熟悉某段代码或需要理解它在整体中的定位时使用。触发词：退一步看看、全局视角、代码全景。",
        triggers=["退一步看看", "全局视角", "代码全景"],
        script_path=str(P3_WORKSPACE_ROOT / "zoom-out/SKILL.md"),
        layer=12
    ),
    "grill_me": ModuleInfo(
        name="grill_me",
        module_type=ModuleType.ENGINEERING_SKILLS,
        description="方案审问循环。让 AI 反复追问你的计划和设计，直到所有分支决策都澄清。触发词：考考我、审审这个想法、方案推敲。",
        triggers=["考考我", "审审这个想法", "方案推敲"],
        script_path=str(P3_WORKSPACE_ROOT / "grill-me/SKILL.md"),
        layer=12
    ),
    # 效率类技能（Layer 10）
    "caveman": ModuleInfo(
        name="caveman",
        module_type=ModuleType.PRODUCTIVITY_SKILLS,
        description="超压缩通信模式。砍掉约 75% 的废话 token，保留全部技术信息。触发词：caveman 模式、简洁回复、省 token。",
        triggers=["caveman", "简洁回复", "省token"],
        script_path=str(P3_WORKSPACE_ROOT / "caveman/SKILL.md"),
        layer=10
    ),
    "handoff": ModuleInfo(
        name="handoff",
        module_type=ModuleType.PRODUCTIVITY_SKILLS,
        description="会话交接。将当前对话压缩为交接文档，供后续会话或其他 AI 继续。触发词：交接、handoff、会话交接、转交。",
        triggers=["交接", "handoff", "会话交接", "转交"],
        script_path=str(P3_WORKSPACE_ROOT / "handoff/SKILL.md"),
        layer=9
    ),
    "write_a_skill": ModuleInfo(
        name="write_a_skill",
        module_type=ModuleType.PRODUCTIVITY_SKILLS,
        description="按规范编写新的 OpenClaw Skill。支持结构模板、渐进式详细说明、资源打包。触发词：写个 skill、创建技能、skill 开发。",
        triggers=["写个skill", "创建技能", "skill开发"],
        script_path=str(P3_WORKSPACE_ROOT / "write-a-skill/SKILL.md"),
        layer=14
    ),
}

# 合并 P3 扩展模块
MODULE_REGISTRY.update(EXTENDED_MODULES_P3)


# ==================== P1: 扩展工作流 ====================

EXTENDED_WORKFLOWS_P1 = {
    "fast_generation": [
        ("rag_cache", "检查缓存"),
        ("semantic_cache", "语义缓存查询"),
        ("hallucination_guard", "输出验证")
    ],
    "long_conversation": [
        ("conversation", "加载对话历史"),
        ("context_compressor", "压缩上下文"),
        ("memory_core", "召回相关记忆"),
        ("adaptive_memory", "优化参数")
    ],
    "knowledge_sync": [
        ("brain_sync", "同步知识库"),
        ("memory_ontology_bridge", "更新知识图谱"),
        ("synapse_network", "创建关联"),
        ("emotion_memory", "计算重要性")
    ],
    "cached_recall": [
        ("rag_cache", "缓存命中检查"),
        ("semantic_cache", "语义相似查询"),
        ("crag_pipeline", "CRAG检索"),
        ("rag_cache", "缓存结果")
    ],
}


# ==================== 全局工作流定义 ====================


# ==================== 全局工作流定义（完整版）====================
# 覆盖所有 129 个模块

INTEGRATED_WORKFLOWS = {
    # ==================== 记忆核心工作流 ====================
    "learn_from_mistake": [
        ("reflection_nl", "处理用户纠正"),
        ("memory_reflector", "记录错误"),
        ("synapse_network", "创建记忆神经元"),
        ("emotion_memory", "计算情感权重"),
        ("adaptive_memory", "优化参数")
    ],
    "error_learning": [
        ("reflection_nl", "处理用户纠正"),
        ("memory_reflector", "记录错误"),
        ("synapse_network", "创建记忆神经元"),
        ("emotion_memory", "计算情感权重"),
        ("adaptive_memory", "优化参数")
    ],
    "neural_plasticity": [
        ("synapse_network", "激活神经元"),
        ("adaptive_ltp_ltd", "LTP/LTD调整"),
        ("emotion_memory", "情感权重更新"),
        ("adaptive_memory", "参数自适应")
    ],

    # ==================== MemGPT 风格工作流 ====================
    "memgpt_recall": [
        ("memgpt_memory", "核心记忆检索"),
        ("memory_stream", "记忆流查询"),
        ("retrieval_formula", "重要性评分"),
        ("context_compressor", "上下文压缩")
    ],
    "memgpt_archive": [
        ("memory_stream", "添加到记忆流"),
        ("memory_bank", "归档存储"),
        ("context_compressor", "压缩核心记忆")
    ],

    # ==================== Generative Agents 工作流 ====================
    "agent_reflect": [
        ("memory_stream", "检索相关记忆"),
        ("retrieval_formula", "计算重要性"),
        ("reflection_engine", "生成反思"),
        ("planning_engine", "更新计划")
    ],
    "agent_plan": [
        ("memory_stream", "回顾历史"),
        ("reflection_engine", "分析现状"),
        ("planning_engine", "制定计划")
    ],

    # ==================== Self-RAG 工作流 ====================
    "self_rag_query": [
        ("isrel_predictor", "预测检索必要性"),
        ("retrieval_evaluator", "评估检索质量"),
        ("knowledge_refiner", "精炼知识"),
        ("isuse_predictor", "评估生成可靠性")
    ],
    "adaptive_retrieval": [
        ("isrel_predictor", "检索必要性预测"),
        ("issup_predictor", "检索支持度预测"),
        ("knowledge_augmentor", "知识补充"),
        ("knowledge_refiner", "知识精炼")
    ],

    # ==================== 知识图谱工作流 ====================
    "kg_build": [
        ("graph_constructor", "构建图谱"),
        ("graphsage_layer", "GraphSAGE嵌入"),
        ("gat_layer", "GAT注意力"),
        ("relation_predictor", "关系预测")
    ],
    "kg_query": [
        ("graph_constructor", "查询图谱"),
        ("gat_layer", "注意力检索"),
        ("relation_predictor", "关系推理")
    ],

    # ==================== 检索增强工作流 ====================
    "recall": [
        ("synapse_network", "激活相关记忆"),
        ("emotion_memory", "情感权重排序"),
        ("hallucination_guard", "验证记忆准确性"),
        ("self_rag", "评估检索必要性"),
        ("crag", "纠正和补充")
    ],
    "enhanced_recall": [
        ("hybrid_search", "混合检索"),
        ("crag_pipeline", "CRAG纠错"),
        ("adaptive_rrf", "RRF融合"),
        ("rag_cache", "缓存结果")
    ],
    "safe_generation": [
        ("hallucination_guard", "生成前验证"),
        ("self_rag", "检索决策"),
        ("crag", "知识精炼"),
        ("adaptive_hallucination_params", "动态阈值"),
        ("hallucination_guard", "输出验证")
    ],
    "cached_recall": [
        ("rag_cache", "缓存命中检查"),
        ("semantic_cache", "语义相似查询"),
        ("crag_pipeline", "CRAG检索"),
        ("rag_cache", "缓存结果")
    ],
    "proposition_recall": [
        ("proposition_retriever", "命题提取"),
        ("hybrid_search", "混合检索"),
        ("adaptive_rrf", "结果融合")
    ],
    "multimodal_recall": [
        ("multimodal_search", "多模态检索"),
        ("visual_generation", "视觉呈现"),
        ("emotion_memory", "情感排序")
    ],
    "cross_lingual_recall": [
        ("cross_lingual", "跨语言翻译"),
        ("hybrid_search", "混合检索"),
        ("adaptive_rrf", "结果融合")
    ],

    # ==================== 向量优化工作流 ====================
    "vector_index": [
        ("ann_selector", "选择ANN算法"),
        ("opq_quantization", "OPQ量化"),
        ("sparse_anns", "稀疏索引"),
        ("vector_api", "向量操作")
    ],
    "vector_search": [
        ("ann_selector", "选择算法"),
        ("approximate_cache", "近似缓存"),
        ("vector_api", "向量检索")
    ],

    # ==================== LLM 优化工作流 ====================
    "fast_generation": [
        ("rag_cache", "检查缓存"),
        ("semantic_cache", "语义缓存查询"),
        ("hallucination_guard", "输出验证")
    ],
    "llm_optimize": [
        ("model_router", "模型路由"),

        ("speculative_decoder", "投机解码"),
        ("streaming_llm", "流式生成")
    ],
    "smart_llm_call": [
        ("model_router", "选择模型"),
        ("llm_client", "调用LLM"),
        ("semantic_cache", "缓存响应"),
        ("model_performance", "记录性能")
    ],

    # ==================== 缓存工作流 ====================
    "cache_warmup": [
        ("rag_cache", "预热RAG缓存"),
        ("semantic_cache", "预热语义缓存"),
        ("approximate_cache", "预热近似缓存"),
        ("computational_storage", "计算存储优化")
    ],
    "cache_manage": [
        ("cache_allocator", "缓存分配"),
        ("cache_aware_scheduler", "缓存调度"),
        ("unified_cache", "统一缓存管理")
    ],

    # ==================== 硬件优化工作流 ====================
    "hardware_detect": [
        ("hardware_optimize", "硬件检测"),
        ("gpu_optimizer", "GPU优化"),
        ("numa_optimizer", "NUMA优化"),
        ("mkl_accelerator", "MKL加速")
    ],
    "hardware_tune": [
        ("numa_optimizer", "NUMA调优"),
        ("cxl_optimizer", "CXL优化"),
        ("hugepage_manager", "大页内存"),
        ("io_optimizer", "IO优化"),
        ("power_manager", "电源管理")
    ],
    "realtime_tune": [
        ("realtime_scheduler", "实时调度"),
        ("irq_isolator", "IRQ隔离"),
        ("cache_allocator", "缓存分配")
    ],

    # ==================== 系统可靠性工作流 ====================
    "health_check": [
        ("failover", "故障检测"),
        ("full_recovery", "恢复检查"),
        ("sandbox_manager", "沙箱状态"),
        ("safety_alignment", "安全检查")
    ],
    "failover_recover": [
        ("failover", "故障转移"),
        ("full_recovery", "完整恢复"),
        ("module_coordinator", "模块重启")
    ],

    # ==================== 会话管理工作流 ====================
    "long_conversation": [
        ("conversation", "加载对话历史"),
        ("context_compressor", "压缩上下文"),
        ("memory_core", "召回相关记忆"),
        ("adaptive_memory", "优化参数")
    ],
    "session_manage": [
        ("conversation", "会话管理"),
        ("context_compressor", "上下文压缩"),
        ("acp_server", "ACP协作")
    ],

    # ==================== Persona 工作流 ====================
    "persona_update": [
        ("auto_update_persona", "自动更新"),
        ("update_persona", "更新画像"),
        ("update_l3_profile", "更新L3"),
        ("smart_memory_update", "智能更新")
    ],
    "preference_learn": [
        ("auto_learner", "自动学习"),
        ("importance_scorer", "重要性评分"),
        ("smart_forgetter", "智能遗忘"),
        ("update_persona", "更新偏好")
    ],

    # ==================== NLP 工作流 ====================
    "nlp_process": [
        ("nlp_processor", "NLP处理"),
        ("nlp_integration", "NLP整合"),
        ("importance_scorer", "重要性评分")
    ],
    "text_analyze": [
        ("nlp_processor", "分词/实体/关键词"),
        ("emotion_memory", "情感分析"),
        ("importance_scorer", "重要性评分")
    ],

    # ==================== 集成工作流 ====================
    "knowledge_sync": [
        ("brain_memory_sync", "同步知识库"),
        ("memory_ontology_bridge", "更新知识图谱"),
        ("synapse_network", "创建关联"),
        ("emotion_memory", "计算重要性")
    ],
    "full_recall": [
        ("xiaoyi_claw_api", "smart_recall 入口"),
        ("crag_pipeline", "CRAG纠错"),
        ("hybrid_search", "混合检索"),
        ("skill_coordinator", "技能协调"),
        ("adaptive_rrf", "结果融合")
    ],

    # ==================== 优化工作流 ====================
    "optimization_run": [
        ("optimization_integration", "优化集成"),
        ("adaptive_hallucination_params", "防幻觉参数"),
        ("dynamic_crag_threshold", "CRAG阈值"),
        ("adaptive_ltp_ltd", "LTP/LTD"),
        ("adaptive_rrf", "RRF权重"),
        ("intelligent_thinking_trigger", "思考触发")
    ],
    "heartbeat_execute": [
        ("heartbeat_task_executor", "心跳执行器"),
        ("autonomous_integrator", "自主任务"),
        ("rules_manager", "规则管理"),
        ("enhanced_hallucination_guard", "增强防幻觉")
    ],

    # ==================== 多模态工作流 ====================
    "multi_modal_recall": [
        ("synapse_network", "关联检索"),
        ("visual_generation", "视觉呈现"),
        ("emotion_memory", "情感排序"),
        ("adaptive_rrf", "结果融合")
    ],
    "image_understand": [
        ("multimodal_memory", "多模态记忆"),
        ("multimodal_search", "多模态检索"),
        ("visual_generation", "可视化")
    ],

    # ==================== 分布式工作流 ====================
    "distributed_recall": [
        ("distributed_search", "分布式检索"),
        ("multiresolution_search", "多分辨率"),
        ("adaptive_rrf", "结果融合")
    ],

    # ==================== 工具注册工作流 ====================
    "tool_register": [
        ("tools_registry", "注册工具"),
        ("auto_tuner", "自动调优"),
        ("module_coordinator", "模块协调")
    ],
    "resource_orchestrate": [
        ("resource_orchestrator", "资源编排"),
        ("cache_allocator", "缓存分配"),
        ("power_manager", "电源管理")
    ],
}


# 合并扩展工作流

INTEGRATED_WORKFLOWS.update(EXTENDED_WORKFLOWS_P1)
