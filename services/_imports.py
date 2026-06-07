"""
GalaxyOS 模块导入辅助

统一管理选装模块的降级导入，替代分散在各文件中的 try/except ImportError 模式。
所有可选模块统一在此处管理，调用方直接 import _imports 获取状态。
"""
import logging

logger = logging.getLogger(__name__)

# ── 10 论文方向模块 ──
try:
    from retrieval_hub import retrieval_hub, _decompose_query, classify_query_complexity
    HAS_RETRIEVAL_HUB = True
except ImportError:
    HAS_RETRIEVAL_HUB = False

try:
    from paper_integration import get_integration, PaperIntegration
    HAS_PAPER_INT = True
except ImportError:
    HAS_PAPER_INT = False

try:
    from adaptive_classifier import AdaptiveClassifier
    HAS_ADAPTIVE = True
except ImportError:
    HAS_ADAPTIVE = False

try:
    from tree_of_thought import TreeOfThought
    HAS_TOT = True
except ImportError:
    HAS_TOT = False

try:
    from memory_editor import MemoryEditor
    HAS_MEMEDITOR = True
except ImportError:
    HAS_MEMEDITOR = False

try:
    from causal_reasoning import CausalReasoning
    HAS_CAUSAL = True
except ImportError:
    HAS_CAUSAL = False

try:
    from cognitive_load import CognitiveLoad
    HAS_COGLOAD = True
except ImportError:
    HAS_COGLOAD = False

try:
    from hyper_routing import HyperRouter, extract_features as hyper_features
    HAS_HYPER = True
except ImportError:
    HAS_HYPER = False

try:
    from plan_solve import PlanSolve
    HAS_PLAN = True
except ImportError:
    HAS_PLAN = False


# ── 增强模块（不可用时设为 None） ──
try:
    from four_advancements import FourAdvancements as PaperEngines
except ImportError:
    PaperEngines = None

try:
    from dynamic_confidence import DynamicConfidence, get_dynamic_confidence
except ImportError:
    DynamicConfidence = None
    get_dynamic_confidence = lambda: None

try:
    from multi_agent_debate import DebateEngine, get_debate_engine
except ImportError:
    DebateEngine = None
    get_debate_engine = lambda: None

try:
    from graph_of_thoughts import GraphOfThoughts, get_got_engine
except ImportError:
    GraphOfThoughts = None
    get_got_engine = lambda: None

try:
    from memory_editor import MemoryEditor, get_memory_editor
except ImportError:
    get_memory_editor = lambda: None

try:
    from hierarchical_context import ContextLayer, get_context_layer
except ImportError:
    ContextLayer = None
    get_context_layer = lambda: None

try:
    from fast_pil import FastPIL, get_fast_pil
except ImportError:
    FastPIL = None
    get_fast_pil = lambda: None


# ── ncps 神经网络模块 ──
try:
    from memory_synapse_network import (
        MemorySynapseNetwork, SynapseNetwork, NeuronManager, SynapseManager
    )
    HAS_NEURAL = True
except ImportError as _ne:
    HAS_NEURAL = False
    logger.debug(f"ncps 模块导入失败: {_ne}")
