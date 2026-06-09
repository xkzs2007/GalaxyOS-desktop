#!/usr/bin/env python3
"""验证重构后的导入正确性 — 不依赖 pytest"""
import sys, os, traceback
from pathlib import Path

WORKSPACE = Path("/workspace")
sys.path.insert(0, str(WORKSPACE))

PASS = 0
FAIL = 0
SKIP = 0

def test_import(module_name, description="", from_path=None):
    """测试一个模块能否被导入"""
    global PASS, FAIL, SKIP
    label = description or module_name
    try:
        if from_path:
            sys.path.insert(0, from_path)
        __import__(module_name)
        PASS += 1
        print(f"  ✓ {label}")
        return True
    except ImportError as e:
        # 第三方库缺失（如 torch）是可预期的
        if any(x in str(e).lower() for x in ['torch', 'ncps', 'jieba', 'onnx', 'numpy', 'scipy', 'pandas', 'faiss', 'qdrant', 'pymilvus', 'httpx', 'requests']):
            SKIP += 1
            print(f"  ~ {label} (第三方库未安装: {str(e).split(chr(10))[0][:60]})")
            return None
        FAIL += 1
        print(f"  ✗ {label} FAILED: {str(e).split(chr(10))[0][:80]}")
        return False
    except Exception as e:
        FAIL += 1
        print(f"  ✗ {label} ERROR: {str(e).split(chr(10))[0][:80]}")
        traceback.print_exc()
        return False

# ══════════════════════════════════════════════════════════════
print("=" * 60)
print("1. 核心模块导入测试")
print("=" * 60)

# path_resolver (新创建)
test_import("path_resolver", "path_resolver (NEW)")

# 验证 path_resolver 常量
try:
    from path_resolver import (
        WORKSPACE_ROOT, OPENCLAW_HOME, SKILLS_DIR, LEARNINGS_DIR,
        DAG_DB, DAG_HNSW_IDX, TEMPORAL_KG_DB, COGNITIVE_MAP_DB,
        OPENCLAW_CONFIG, CLAW_CORE_DIST, CLAW_CORE_VAR, CLAW_SHARED_STATE,
        LLM_MEMORY_CORE, LLM_MEMORY_CONFIG, LLM_CONFIG_JSON, LLM_CONFIG_EXAMPLE,
        SEEDREAM_SCRIPT, XIAOYI_WEB_SEARCH_SCRIPT, GENERATED_IMAGES,
        VERIFIED_MEMORIES, ONTOLOGY_JSON, EMOTION_TRACK, SYNAPSE_NETWORK,
        get_vec_extension_path, get_skill_path,
    )
    # 验证类型
    assert isinstance(WORKSPACE_ROOT, Path), f"WORKSPACE_ROOT should be Path, got {type(WORKSPACE_ROOT)}"
    assert ".openclaw" in str(OPENCLAW_HOME), f"OPENCLAW_HOME should contain .openclaw"
    assert "skills" in str(SKILLS_DIR), f"SKILLS_DIR should contain skills"
    assert str(DAG_DB).endswith("dag_context.db"), f"DAG_DB should end with dag_context.db"
    print("  ✓ path_resolver 常量验证 (25+ constants)")
    PASS += 1
except Exception as e:
    FAIL += 1
    print(f"  ✗ path_resolver 常量验证 FAILED: {e}")

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("2. Services 模块导入测试")
print("=" * 60)

SERVICES = WORKSPACE / "services"
CORE = WORKSPACE / "skills" / "llm-memory-integration" / "core"
sys.path.insert(0, str(SERVICES))
sys.path.insert(0, str(CORE))

test_modules = [
    "retrieval_hub", "dag_context_manager", "dag_shim", "temporal_kg",
    "hallucination_guard", "enhanced_hallucination_guard",
    "claw_worker", "claw_helpers", "xiaoyi_memory",
    "memory_unified", "memory_consolidation", "memory_editor",
    "memory_synapse_network",
    "smart_processor", "capability_registry", "cognitive_map",
    "unified_entry", "blob_arena", "gateway_client",
    "dynamic_confidence", "four_advancements",
    "rules_manager", "resilience_system",
    "unified_coordinator", "thinking_enhanced",
    "visual_generation", "performance_patch", "onnx_embedding",
    "v4_services", "adaptive_classifier",
    "spatial_topology", "hierarchical_context", "hyper_routing",
    "multi_agent_debate", "paper_integration", "nlp_enhanced",
    "cross_lingual", "embedding_enhance", "emotion_memory",
    "hierarchical_memory", "hybrid_memory_search",
    "importance_scorer", "memgpt_memory", "memory_bank",
    "memory_reflector", "model_performance",
    "multimodal_search", "neural_pipeline",
    "persona_methodology", "platform_adapter",
    "reranker", "smart_forgetter", "smart_memory_update",
    "sqlite_ext", "sqlite_vec", "task_memory_bridge",
    "update_l3_profile", "update_persona",
    "vector_store", "unified_vector_store",
    "gnn_graph_builder", "heartbeat_task_executor",
    "kora_behavior", "knowledge_asset",
    "biorhythm_sleep_consolidation", "auto_learner",
    "auto_update_persona", "autonomous_integrator",
    "dreaming_bridge", "hallucination_integration",
]

for mod in test_modules:
    test_import(mod, f"services/{mod}")

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("3. Scripts 模块导入测试")
print("=" * 60)

SCRIPTS = WORKSPACE / "scripts"
SCRIPT_FILES = WORKSPACE / "skills" / "llm-memory-integration" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPT_FILES))

script_modules = [
    "llm_client", "search", "auto_backup", "auto_recovery",
    "auto_update_persona", "cached_search",
    "channel_health", "check_coverage",
    "full_opt_search", "opt_search",
    "health_monitor", "hybrid_memory_search",
    "model_performance", "one_click_setup",
    "one_click_vector_setup", "optimize_vector_system",
    "rebuild_fts", "run_maintenance",
    "safe_db", "safe_extension_loader",
    "security_audit", "setup_maintenance",
    "skill_version_check", "smart_memory_update",
    "smart_memory_upgrade", "three_engine_manager",
    "ultimate_search", "unified_logger",
    "update_l3_profile", "update_persona",
    "vector_coverage_monitor", "vector_system_optimizer",
]

for mod in script_modules:
    test_import(mod, f"scripts/{mod}")

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("4. Workspace-scripts 导入测试")
print("=" * 60)

WS_SCRIPTS = WORKSPACE / "workspace-scripts"
sys.path.insert(0, str(WS_SCRIPTS))

ws_modules = [
    "backfill_scene_trace", "blob_arena", "bridge_sync",
    "capability_registry", "claw_worker", "dag_context_manager",
    "dag_shim", "data_bridge", "knowledge_asset",
    "self_evolution_engine",
]

for mod in ws_modules:
    test_import(mod, f"workspace-scripts/{mod}")

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("5. 文件级语法检查 (抽样 50 个修改文件)")
print("=" * 60)

import subprocess
changed = subprocess.run(
    ["git", "diff", "--name-only"], capture_output=True, text=True, cwd=str(WORKSPACE)
).stdout.strip().split('\n')
py_files = [f for f in changed if f.endswith('.py')]

import random
sample = random.sample(py_files, min(50, len(py_files)))
syntax_ok = 0
syntax_fail = 0
for f in sample:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(WORKSPACE / f)],
        capture_output=True, text=True, cwd=str(WORKSPACE)
    )
    if result.returncode == 0:
        syntax_ok += 1
    else:
        syntax_fail += 1
        print(f"  ✗ SYNTAX ERROR: {f}")
        print(f"    {result.stderr.strip()[:100]}")

print(f"  ✓ 语法检查: {syntax_ok}/{syntax_ok + syntax_fail} 通过")

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"📊 总计: {PASS} 通过, {FAIL} 失败, {SKIP} 跳过 (第三方库未安装)")
print("=" * 60)

if FAIL > 0:
    print(f"\n⚠️  {FAIL} 个测试失败!")
    sys.exit(1)
else:
    print("\n✅ 所有可执行测试通过!")
