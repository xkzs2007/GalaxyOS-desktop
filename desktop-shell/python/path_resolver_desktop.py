"""path_resolver_desktop.py — Desktop-app shim for GalaxyOS' path_resolver.

This module REPLACES ``path_resolver`` (the OpenClaw-coupled legacy module
at the repo root) for the GalaxyOS desktop app. It is installed into
``sys.modules['path_resolver']`` by the sidecar before any GalaxyOS code
imports the original.

The shim mirrors the same public constants as the original
``path_resolver.py`` so the 60+ downstream call-sites continue to work
unchanged. The differences are:

1. **Default HOME** is ``~/.<APP>/`` (e.g. ``~/.galaxyos/``) instead of
   ``~/.openclaw/``. Override with ``GALAXYOS_HOME`` env var.
2. **OpenClaw fallback**: if ``OPENCLAW_HOME`` is set and points to a
   valid OpenClaw workspace, the shim honours that (so OpenClaw users
   keep their existing data).
3. **All paths are pure ``pathlib.Path``** — same as the original.

This is the *only* Python file in the desktop shell that touches
filesystem layout. The rest of the sidecar (galaxyos_sidecar.py) treats
it as a black box.

Public surface (must stay compatible with upstream path_resolver.py):

    OPENCLAW_HOME, WORKSPACE_ROOT, GALAXYOS_REPO,
    GALAXYOS_PKG, GALAXYOS_ENGINE, GALAXYOS_PRIVILEGED,
    GALAXYOS_ORCH, GALAXYOS_CONFIG, GALAXYOS_SCRIPTS,
    SKILLS_DIR, LEARNINGS_DIR, GALAXYOS_DIR, NEURAL_CACHE_DIR,
    GENERATED_IMAGES,
    DAG_DB, DAG_HNSW_IDX, DAG_BLOB_ARENA, TEMPORAL_KG_DB,
    COGNITIVE_MAP_DB, MEMORY_TDAI_DIR, VECTORS_DB, MEMORY_TDAI_CONFIG,
    EMOTION_TRACK, VERIFIED_MEMORIES, ONTOLOGY_JSON, SYNAPSE_NETWORK,
    OPENCLAW_CONFIG, XIAOYIENV_FILE,
    EXTENSIONS_DIR, GALAXYOS_EXT_DIR, GALAXYOS_EXT_VAR,
    CLAW_CORE_DIST, CLAW_CORE_VAR, CLAW_SHARED_STATE, RCI_SHARED_STATE,
    SCRIPTS_DIR, SYNC_CLAW_SCRIPT,
    SQLITE_VEC_TENCENTDB, SQLITE_VEC_NODE, SQLITE_VEC_PY312,
    LLM_MEMORY_DIR, LLM_MEMORY_CORE, LLM_MEMORY_CONFIG,
    LLM_MEMORY_SCRIPTS, LLM_MEMORY_PRIVILEGED,
    LLM_CONFIG_JSON, LLM_CONFIG_EXAMPLE,
    XIAOYI_OMEGA_DIR, XIAOYI_OMEGA_SKILLS, XIAOYI_OMEGA_LLM_CORE,
    XIAOYI_OMEGA_CONFIG, XIAOYI_OMEGA_LLM_CONFIG, XIAOYI_OMEGA_SCRIPTS,
    SEEDREAM_SCRIPT, XIAOYI_WEB_SEARCH_SCRIPT, TODAY_TASK_DIR,
    HUAWEI_DRIVE_SCRIPT,
    GALAXYOS_MODELS, GALAXYOS_DATA, GALAXYOS_EMBEDDINGS,
    GALAXYOS_CAPABILITY,
    LOG_DIR, AUDIT_LOG_DIR, KORA_BEHAVIOR_DB, OPENCLAW_MEMORY_DIR,
    STR,
    get_vec_extension_path, get_vectors_db_path, get_skill_path,
    ensure_dirs
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Home resolution ─────────────────────────────────────────────────────
# Priority: GALAXYOS_HOME (desktop) > OPENCLAW_HOME (interop) > ~/.galaxyos

_DESKTOP_HOME = Path(
    os.environ.get("GALAXYOS_HOME", Path.home() / ".galaxyos")
)
_OPENCLAW_HOME_ENV = os.environ.get("OPENCLAW_HOME")


def _resolve_home() -> Path:
    """Pick the right home directory.

    Order:
      1. Explicit ``GALAXYOS_HOME`` env var (always wins).
      2. If ``OPENCLAW_HOME`` is set and points to a real OpenClaw
         workspace (i.e. ``extensions/`` exists), use that — desktop app
         is then a *front-end* to an existing OpenClaw install.
      3. Otherwise the desktop default ``~/.<APP>/``.
    """
    if os.environ.get("GALAXYOS_HOME"):
        return _DESKTOP_HOME

    if _OPENCLAW_HOME_ENV:
        oc = Path(_OPENCLAW_HOME_ENV)
        if (oc / "extensions").exists() or (oc / "openclaw.json").exists():
            logger.info(
                "Desktop shim: detected OpenClaw at %s — using it as home.",
                oc,
            )
            return oc

    return _DESKTOP_HOME


OPENCLAW_HOME: Path = _resolve_home()
WORKSPACE_ROOT: Path = Path(
    os.environ.get("OPENCLAW_WORKSPACE", OPENCLAW_HOME / "workspace")
)
_GALAXYOS_REPO: Path = Path(
    os.environ.get(
        "GALAXYOS_REPO",
        # __file__ is desktop-shell/python/path_resolver_desktop.py,
        # so .parent.parent.parent = desktop-shell/.. = galaxyos/ repo root.
        Path(__file__).resolve().parent.parent.parent,
    )
)


# ── GalaxyOS package layout (mirrors upstream) ──────────────────────────
GALAXYOS_PKG        = _GALAXYOS_REPO / "galaxyos"
GALAXYOS_ENGINE     = GALAXYOS_PKG / "engine"
GALAXYOS_PRIVILEGED = GALAXYOS_PKG / "privileged"
GALAXYOS_ORCH       = GALAXYOS_PKG / "orchestration"
GALAXYOS_CONFIG     = GALAXYOS_PKG / "config"
GALAXYOS_SCRIPTS    = GALAXYOS_PKG / "scripts"


# ── Top-level derived directories ───────────────────────────────────────
SKILLS_DIR        = WORKSPACE_ROOT / "skills"
LEARNINGS_DIR     = WORKSPACE_ROOT / ".learnings"
GALAXYOS_DIR      = WORKSPACE_ROOT / "GalaxyOS"  # deprecated alias
NEURAL_CACHE_DIR  = WORKSPACE_ROOT / ".neural_cache"
GENERATED_IMAGES  = WORKSPACE_ROOT / "generated-images"

# Desktop-app-specific subdirs (new; not in upstream path_resolver)
ROUTER_MEMORY_DIR = WORKSPACE_ROOT / "router_memory"   # ACRouter 20K FIFO
MODELS_DIR        = WORKSPACE_ROOT / "models"          # MeMo / BGE / Orchestrator
HEARTBEAT_DIR     = WORKSPACE_ROOT / "heartbeat"
DESKTOP_LOGS_DIR  = WORKSPACE_ROOT / "logs" / "desktop"


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


# ── Extensions (kept for OpenClaw interop; no-op in pure desktop mode) ─
EXTENSIONS_DIR    = OPENCLAW_HOME / "extensions"
GALAXYOS_EXT_DIR  = EXTENSIONS_DIR / "galaxyos"
GALAXYOS_EXT_VAR  = GALAXYOS_EXT_DIR / "var"
CLAW_CORE_DIST    = GALAXYOS_ENGINE
CLAW_CORE_VAR     = EXTENSIONS_DIR / "claw-core" / "var"
CLAW_SHARED_STATE = GALAXYOS_EXT_VAR / "claw_shared_state"
RCI_SHARED_STATE  = GALAXYOS_EXT_VAR / "rci_shared_state"


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


# ── Backward-compat aliases (must match upstream exactly) ──────────────
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
OPENCLAW_MEMORY_DIR      = OPENCLAW_HOME  # deprecated alias


# ── Convenience: str paths (matches upstream STR dict shape) ──────────
def _str_paths() -> dict:
    g = globals()
    return {k: str(v) for k, v in g.items()
            if k.isupper() and isinstance(v, (Path, str))}


STR = _str_paths()


# ── Utility (signatures must match upstream) ───────────────────────────
def get_vec_extension_path() -> Path:
    """Locate the sqlite-vec native extension on disk.

    Desktop mode: there is no OpenClaw-installed vec0.so; the desktop
    app uses pure-Python fallback (HNSWLib from requirements-core).
    Returns the first existing path, or SQLITE_VEC_TENCENTDB as a
    sentinel default.
    """
    for p in (SQLITE_VEC_TENCENTDB, SQLITE_VEC_NODE, SQLITE_VEC_PY312):
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


def install_into_sys_modules() -> None:
    """Install this shim as the ``path_resolver`` module in sys.modules.

    Idempotent. Call this BEFORE any GalaxyOS import that pulls in
    ``path_resolver`` (60+ files in upstream).
    """
    sys.modules["path_resolver"] = sys.modules[__name__]
    if os.environ.get("GALAXYOS_DESKTOP_DEBUG"):
        logger.setLevel(logging.DEBUG)
        logger.debug("Desktop path_resolver shim installed: HOME=%s", OPENCLAW_HOME)


# ── Auto-install when imported directly (sidecar pattern) ─────────────
# When the sidecar does `import path_resolver_desktop`, the shim
# registers itself. This lets the sidecar simply do:
#     import path_resolver_desktop   # installs
#     import galaxyos.engine.xiaoyi_claw_api  # works
if __name__ != "__main__":
    install_into_sys_modules()
