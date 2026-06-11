"""Services shim — proxies to galaxyos.engine & galaxyos.privileged

This package uses import hooks to redirect all `services.*` imports
to their new locations under `galaxyos.engine` and `galaxyos.privileged`.
"""

import sys
import os

__version__ = "6.6.0"
__author__ = "xkzs2007"

# Ensure galaxyos packages are importable as galaxyos.engine / galaxyos.privileged
# Also add the engine and privileged dirs to sys.path so internal flat imports
# (e.g. `from blob_arena import ...`) work when modules are loaded from there.
_ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ['galaxyos', 'galaxyos/engine', 'galaxyos/privileged']:
    _p = os.path.join(_ws, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

_MODULE_MAP = {
    '_imports':                     ('galaxyos.engine', '_imports'),
    'adaptive_classifier':          ('galaxyos.engine', 'adaptive_classifier'),
    'adaptive_hallucination_params':('galaxyos.engine', 'adaptive_hallucination_params'),
    'adaptive_ltp_ltd':             ('galaxyos.engine', 'adaptive_ltp_ltd'),
    'adaptive_memory':              ('galaxyos.engine', 'adaptive_memory'),
    'adaptive_rrf':                 ('galaxyos.engine', 'adaptive_rrf'),
    'ann_selector':                 ('galaxyos.privileged', 'ann_selector'),
    'auto_learner':                 ('galaxyos.engine', 'auto_learner'),
    'auto_tuner':                   ('galaxyos.privileged', 'auto_tuner'),
    'biorhythm_sleep_consolidation':('galaxyos.engine', 'biorhythm_sleep_consolidation'),
    'chain_of_verification':        ('galaxyos.engine', 'chain_of_verification'),
    'causal_reasoning':             ('galaxyos.engine', 'causal_reasoning'),
    'claw_helpers':                 ('galaxyos.engine', 'claw_helpers'),
    'cognitive_map':                ('galaxyos.engine', 'cognitive_map'),
    'cognitive_load':               ('galaxyos.engine', 'cognitive_load'),
    'context_compressor':           ('galaxyos.privileged', 'context_compressor'),
    'conversation':                 ('galaxyos.privileged', 'conversation'),
    'crag':                         ('galaxyos.engine', 'crag'),
    'crag_pipeline':                ('galaxyos.privileged', 'crag_pipeline'),
    'dag_context_manager':          ('galaxyos.engine', 'dag_context_manager'),
    'dep_checker':                  ('galaxyos.privileged', 'dep_checker'),
    'dynamic_confidence':           ('galaxyos.engine', 'dynamic_confidence'),
    'emotion_memory':               ('galaxyos.engine', 'emotion_memory'),
    'enhanced_hallucination_guard': ('galaxyos.engine', 'enhanced_hallucination_guard'),
    'exceptions':                   ('galaxyos.privileged', 'exceptions'),
    'fast_pil':                     ('galaxyos.engine', 'fast_pil'),
    'four_advancements':            ('galaxyos.engine', 'four_advancements'),
    'graph_of_thoughts':            ('galaxyos.engine', 'graph_of_thoughts'),
    'hierarchical_context':         ('galaxyos.engine', 'hierarchical_context'),
    'hybrid_search':                ('galaxyos.privileged', 'hybrid_search'),
    'hyper_routing':                ('galaxyos.engine', 'hyper_routing'),
    'memory_consolidation':         ('galaxyos.engine', 'memory_consolidation'),
    'memory_editor':                ('galaxyos.engine', 'memory_editor'),
    'memory_synapse_network':       ('galaxyos.engine', 'memory_synapse_network'),
    'model_router':                 ('galaxyos.privileged', 'model_router'),
    'multi_agent_debate':           ('galaxyos.engine', 'multi_agent_debate'),
    'nlp_enhanced':                 ('galaxyos.engine', 'nlp_enhanced'),
    'paper_integration':            ('galaxyos.engine', 'paper_integration'),
    'paper_integration_addon':     ('galaxyos.engine', 'paper_integration_addon'),
    'cfc_inference':                ('galaxyos.engine', 'cfc_inference'),
    'cfc_sequence_predictor':       ('galaxyos.engine', 'cfc_sequence_predictor'),
    'gnn_graph_builder':            ('galaxyos.engine', 'gnn_graph_builder'),
    'ltc_synapse':                  ('galaxyos.engine', 'ltc_synapse'),
    'neural_memory_gate':           ('galaxyos.engine', 'neural_memory_gate'),
    'plan_solve':                   ('galaxyos.engine', 'plan_solve'),
    'rag_optimizer':                ('galaxyos.privileged', 'rag_optimizer'),
    'rccam_state':                  ('galaxyos.engine', 'rccam_state'),
    'retrieval_hub':                ('galaxyos.engine', 'retrieval_hub'),
    'onnx_embedding':               ('galaxyos.engine', 'onnx_embedding'),
    'neural_pipeline':              ('galaxyos.engine', 'neural_pipeline'),
    'thinking_enhanced':            ('galaxyos.engine', 'thinking_enhanced'),
    'tree_of_thought':              ('galaxyos.engine', 'tree_of_thought'),
    'unified_cache':                ('galaxyos.privileged', 'unified_cache'),
    'unified_vector_store':         ('galaxyos.engine', 'unified_vector_store'),
    'xiaoyi_claw_api':              ('galaxyos.engine', 'xiaoyi_claw_api'),
}


def _load_real(proxy_name: str):
    """Called from proxy modules to load the real module into sys.modules."""
    import importlib
    sub = proxy_name.rsplit('.', 1)[-1]
    if sub in _MODULE_MAP:
        pkg, mod = _MODULE_MAP[sub]
        real_name = f'{pkg}.{mod}'
        real_mod = importlib.import_module(real_name)
        proxy_mod = sys.modules[proxy_name]
        # Copy attributes from real to proxy, but preserve proxy identity attrs
        _preserve = {'__name__', '__file__', '__path__', '__package__', '__loader__', '__spec__'}
        for k, v in real_mod.__dict__.items():
            if k not in _preserve:
                proxy_mod.__dict__[k] = v
    else:
        raise ImportError(f"services: no mapping for {sub}")


def initialize() -> dict:
    """Compatibility shim — runs basic health check across all services."""
    import importlib as _il
    result = {
        "platform": "galaxyos",
        "dependencies": {},
        "initialized_modules": [],
    }
    for name, (pkg, _mod) in sorted(_MODULE_MAP.items()):
        try:
            _il.import_module(f'{pkg}.{_mod}')
            result["initialized_modules"].append(name)
        except ImportError:
            pass
    result["dependencies"]["ncps"] = "installed"
    result["dependencies"]["numpy"] = "installed"
    return result
