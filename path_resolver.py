#!/usr/bin/env python3
"""
path_resolver.py — Centralized path configuration for GalaxyOS

***** SINGLE SOURCE OF TRUTH *****
All file paths MUST be resolved through this module.
Do NOT hardcode paths anywhere else.

v9.0: GalaxyOS standalone Agent APP. No more OpenClaw coupling.
     - Default home: ~/.galaxyos/  (was ~/.openclaw/ in legacy)
     - OPENCLAW_HOME is still accepted for backward compatibility with
       legacy OpenClaw users, but GALAXYOS_HOME always wins.

Environment variables (override defaults):
  GALAXYOS_HOME      — root directory (default: ~/.galaxyos)
  GALAXYOS_WORKSPACE — workspace directory (default: $GALAXYOS_HOME/workspace)
  OPENCLAW_HOME      — LEGACY root directory (only if GALAXYOS_HOME unset and ~/.openclaw exists)
  OPENCLAW_WORKSPACE — LEGACY workspace directory
  GALAXYOS_REPO      — GalaxyOS git repo (default: auto-detect from __file__)

Usage:
  from path_resolver import (
      WORKSPACE_ROOT, DAG_DB, GALAXYOS_ENGINE, GALAXYOS_PRIVILEGED, ...
  )
"""

import os
import sys
from pathlib import Path

# ── Base paths ──────────────────────────────────────────────────────────
# Priority: GALAXYOS_HOME > OPENCLAW_HOME (legacy) > ~/.galaxyos (default)
# The variable is still named OPENCLAW_HOME for backward compatibility
# (60+ call sites reference it), but its *default value* is now standalone.
_GALAXYOS_HOME = os.environ.get("GALAXYOS_HOME")
_OPENCLAW_HOME_LEGACY = os.environ.get("OPENCLAW_HOME")

if _GALAXYOS_HOME:
    _resolved_home = Path(_GALAXYOS_HOME)
elif _OPENCLAW_HOME_LEGACY and Path(_OPENCLAW_HOME_LEGACY).exists():
    # Legacy OpenClaw interop: if OPENCLAW_HOME is set and points to
    # a real OpenClaw install, use it (so legacy users keep their data).
    _resolved_home = Path(_OPENCLAW_HOME_LEGACY)
else:
    _resolved_home = Path.home() / ".galaxyos"

OPENCLAW_HOME = _resolved_home
# New canonical name (same value, preferred in new code)
GALAXYOS_HOME = _resolved_home

WORKSPACE_ROOT = Path(
    os.environ.get("GALAXYOS_WORKSPACE",
    os.environ.get("OPENCLAW_WORKSPACE", OPENCLAW_HOME / "workspace"))
)
_GALAXYOS_REPO = Path(os.environ.get(
    "GALAXYOS_REPO",
    Path(__file__).resolve().parent  # path_resolver.py is at repo root
))

# ═══════════════════════════════════════════════════════════════════════
# galaxyos/ unified package (NEW — single source of truth for engine code)
# ═══════════════════════════════════════════════════════════════════════
GALAXYOS_PKG        = _GALAXYOS_REPO / "galaxyos"
GALAXYOS_ENGINE     = GALAXYOS_PKG / "engine"
GALAXYOS_PRIVILEGED = GALAXYOS_PKG / "privileged"
GALAXYOS_ORCH       = GALAXYOS_PKG / "orchestration"
GALAXYOS_CONFIG     = GALAXYOS_PKG / "config"
GALAXYOS_SCRIPTS    = GALAXYOS_PKG / "scripts"

# ── Top-level derived directories ──────────────────────────────────────
SKILLS_DIR        = WORKSPACE_ROOT / "skills"
LEARNINGS_DIR     = WORKSPACE_ROOT / ".learnings"
GALAXYOS_DIR      = WORKSPACE_ROOT / "GalaxyOS"  # deprecated → GALAXYOS_PKG
NEURAL_CACHE_DIR  = WORKSPACE_ROOT / ".neural_cache"
GENERATED_IMAGES  = WORKSPACE_ROOT / "generated-images"

# ── Databases ───────────────────────────────────────────────────────────
DAG_DB            = OPENCLAW_HOME / "dag_context.db"
DAG_HNSW_IDX      = OPENCLAW_HOME / "dag_hnsw.idx"
DAG_BLOB_ARENA    = OPENCLAW_HOME / "dag_blob_arena"
TEMPORAL_KG_DB    = WORKSPACE_ROOT / "temporal_kg.db"
COGNITIVE_MAP_DB  = WORKSPACE_ROOT / "cognitive_map.db"
MEMORY_TDAI_DIR   = OPENCLAW_HOME / "memory-tdai"
VECTORS_DB        = MEMORY_TDAI_DIR / "vectors.db"
MEMORY_TDAI_CONFIG = MEMORY_TDAI_DIR / "config" / "extension_config.json"

# ── Learnings files ─────────────────────────────────────────────────────
EMOTION_TRACK     = LEARNINGS_DIR / "emotion_track.json"
VERIFIED_MEMORIES = LEARNINGS_DIR / "verified_memories.jsonl"
ONTOLOGY_JSON     = LEARNINGS_DIR / "ontology.json"
SYNAPSE_NETWORK   = LEARNINGS_DIR / "synapse_network"

# ── Config files ────────────────────────────────────────────────────────
OPENCLAW_CONFIG   = OPENCLAW_HOME / "openclaw.json"
XIAOYIENV_FILE    = OPENCLAW_HOME / ".xiaoyienv"

# ── Extensions (OpenClaw plugins) ──────────────────────────────────────
EXTENSIONS_DIR    = OPENCLAW_HOME / "extensions"
# galaxyos plugin (NEW)
GALAXYOS_EXT_DIR  = EXTENSIONS_DIR / "galaxyos"
GALAXYOS_EXT_VAR  = GALAXYOS_EXT_DIR / "var"
# claw-core (deprecated backward compat)
CLAW_CORE_DIST    = GALAXYOS_ENGINE
CLAW_CORE_VAR     = EXTENSIONS_DIR / "claw-core" / "var"
CLAW_SHARED_STATE = GALAXYOS_EXT_VAR / "claw_shared_state"    # v7.0: galaxyos/var 优先
RCI_SHARED_STATE  = GALAXYOS_EXT_VAR / "rci_shared_state"      # v7.0: galaxyos/var 优先

# ── Scripts ─────────────────────────────────────────────────────────────
SCRIPTS_DIR       = OPENCLAW_HOME / "scripts"
SYNC_CLAW_SCRIPT  = SCRIPTS_DIR / "sync_claw_code.sh"

# ── SQLite vector extensions ────────────────────────────────────────────
SQLITE_VEC_TENCENTDB = (
    EXTENSIONS_DIR / "memory-tencentdb" / "node_modules" /
    "sqlite-vec-linux-x64" / "vec0.so"
)
SQLITE_VEC_NODE = (
    OPENCLAW_HOME / "node_modules" / "sqlite-vec-linux-x64" / "vec0.so"
)
SQLITE_VEC_PY312 = (
    WORKSPACE_ROOT / "repo" / "lib" / "python3.12" /
    "site-packages" / "sqlite_vec" / "vec0.so"
)

# ── Backward-compat aliases (old → new mapping) ────────────────────────
LLM_MEMORY_DIR           = GALAXYOS_PKG
LLM_MEMORY_CORE          = GALAXYOS_ENGINE
LLM_MEMORY_CONFIG        = GALAXYOS_CONFIG
LLM_MEMORY_SCRIPTS       = GALAXYOS_SCRIPTS
LLM_MEMORY_PRIVILEGED    = GALAXYOS_PRIVILEGED
LLM_CONFIG_JSON          = GALAXYOS_CONFIG / "llm_config.json"
LLM_CONFIG_EXAMPLE       = GALAXYOS_CONFIG / "llm_config.example.json"

XIAOYI_OMEGA_DIR         = GALAXYOS_PKG
XIAOYI_OMEGA_SKILLS      = GALAXYOS_PKG
XIAOYI_OMEGA_LLM_CORE    = GALAXYOS_ENGINE
XIAOYI_OMEGA_CONFIG      = GALAXYOS_CONFIG
XIAOYI_OMEGA_LLM_CONFIG  = GALAXYOS_CONFIG / "llm_config.json"
XIAOYI_OMEGA_SCRIPTS     = GALAXYOS_SCRIPTS

SEEDREAM_SCRIPT          = SKILLS_DIR / "seedream-image_gen" / "scripts" / "generate_seedream.py"
XIAOYI_WEB_SEARCH_SCRIPT = SKILLS_DIR / "xiaoyi-web-search" / "scripts" / "search.js"
TODAY_TASK_DIR           = SKILLS_DIR / "today-task"
HUAWEI_DRIVE_SCRIPT      = SKILLS_DIR / "huawei-drive" / "scripts" / "smart_backup.py"

GALAXYOS_MODELS          = _GALAXYOS_REPO / "models"
GALAXYOS_DATA            = _GALAXYOS_REPO / "data"
GALAXYOS_EMBEDDINGS      = GALAXYOS_MODELS / "embeddings"
GALAXYOS_CAPABILITY      = GALAXYOS_DATA / "capability_registry"

# ── Logs / audit ────────────────────────────────────────────────────────
LOG_DIR                  = OPENCLAW_HOME / "logs"
AUDIT_LOG_DIR            = LOG_DIR / "audit"
KORA_BEHAVIOR_DB         = OPENCLAW_HOME / "kora_behavior.db"
OPENCLAW_MEMORY_DIR      = OPENCLAW_HOME  # deprecated


# ── Convenience: str paths ──────────────────────────────────────────────
def _str_paths():
    g = globals()
    return {k: str(v) for k, v in g.items()
            if k.isupper() and isinstance(v, (Path, str))}

STR = _str_paths()


# ── Utility ─────────────────────────────────────────────────────────────
def get_vec_extension_path() -> Path:
    for p in [SQLITE_VEC_TENCENTDB, SQLITE_VEC_NODE, SQLITE_VEC_PY312]:
        if p.exists():
            return p
    return SQLITE_VEC_TENCENTDB

def get_vectors_db_path() -> Path:
    return VECTORS_DB

def get_skill_path() -> Path:
    return GALAXYOS_PKG

def ensure_dirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)

if os.environ.get("OPENCLAW_DEBUG_PATHS"):
    import logging
    logging.basicConfig(level=logging.DEBUG)
    log = logging.getLogger("path_resolver")
    log.debug("GALAXYOS_PKG     = %s", GALAXYOS_PKG)
    log.debug("GALAXYOS_ENGINE  = %s", GALAXYOS_ENGINE)
