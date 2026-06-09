#!/usr/bin/env python3
"""Batch replace hardcoded paths with path_resolver in all non-dist Python files.

Strategy:
  - Insert path_resolver import BEFORE any code that uses it
  - Replace all expanduser/Path.home patterns with path_resolver constants
  - Handle edge cases like f-strings and shell commands
"""
import os, re, sys
from pathlib import Path

WORKSPACE = Path("/workspace")
SKIP = {"node_modules", ".git", "dist", "__pycache__", ".pytest_cache"}

# ── Path setup code to insert at top of file ──
PATH_SETUP = '''import os as _os, sys as _sys
_ws_root = _os.environ.get("OPENCLAW_WORKSPACE", _os.path.expanduser("~/.openclaw/workspace"))
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
'''

# ── Ordered replacement map ──
REPLACE = [
    # --- os.path.expanduser(...) → path_resolver.CONSTANT ---
    ('os.path.expanduser("~/.openclaw/workspace")',          'path_resolver.WORKSPACE_ROOT'),
    ("os.path.expanduser('~/.openclaw/workspace')",          "path_resolver.WORKSPACE_ROOT"),
    ('os.path.expanduser("~/.openclaw")',                     'path_resolver.OPENCLAW_HOME'),
    ("os.path.expanduser('~/.openclaw')",                     "path_resolver.OPENCLAW_HOME"),
    ('os.path.expanduser("~/.openclaw/dag_context.db")',     'path_resolver.DAG_DB'),
    ("os.path.expanduser('~/.openclaw/dag_context.db')",     "path_resolver.DAG_DB"),
    ('os.path.expanduser("~/.openclaw/dag_hnsw.idx")',       'path_resolver.DAG_HNSW_IDX'),
    ('os.path.expanduser("~/.openclaw/dag_blob_arena")',     'path_resolver.DAG_BLOB_ARENA'),
    ('os.path.expanduser("~/.openclaw/workspace/temporal_kg.db")', 'path_resolver.TEMPORAL_KG_DB'),
    ("os.path.expanduser('~/.openclaw/workspace/temporal_kg.db')", "path_resolver.TEMPORAL_KG_DB"),
    ('os.path.expanduser("~/.openclaw/workspace/cognitive_map.db")', 'path_resolver.COGNITIVE_MAP_DB'),
    ("os.path.expanduser('~/.openclaw/workspace/cognitive_map.db')", "path_resolver.COGNITIVE_MAP_DB"),
    ('os.path.expanduser("~/.openclaw/workspace/skills")',   'path_resolver.SKILLS_DIR'),
    ("os.path.expanduser('~/.openclaw/workspace/skills')",   "path_resolver.SKILLS_DIR"),
    ('os.path.expanduser("~/.openclaw/workspace/.learnings/verified_memories.jsonl")', 'path_resolver.VERIFIED_MEMORIES'),
    ('os.path.expanduser("~/.openclaw/workspace/.learnings/ontology.json")', 'path_resolver.ONTOLOGY_JSON'),
    ('os.path.expanduser("~/.openclaw/workspace/.learnings/emotion_track.json")', 'path_resolver.EMOTION_TRACK'),
    ('os.path.expanduser("~/.openclaw/workspace/.learnings/synapse_network")', 'path_resolver.SYNAPSE_NETWORK'),
    ('os.path.expanduser("~/.openclaw/extensions/claw-core/dist/scripts")', 'path_resolver.CLAW_CORE_DIST'),
    ('os.path.expanduser("~/.openclaw/extensions/claw-core/var")', 'path_resolver.CLAW_CORE_VAR'),
    ('os.path.expanduser("~/.openclaw/extensions/claw-core/var/claw_shared_state")', 'path_resolver.CLAW_SHARED_STATE'),
    ('os.path.expanduser("~/.openclaw/extensions/claw-core/var/rci_shared_state")', 'path_resolver.RCI_SHARED_STATE'),
    ('os.path.expanduser("~/.openclaw/openclaw.json")',     'path_resolver.OPENCLAW_CONFIG'),
    ("os.path.expanduser('~/.openclaw/openclaw.json')",     "path_resolver.OPENCLAW_CONFIG"),
    ('os.path.expanduser("~/.openclaw/.xiaoyienv")',        'path_resolver.XIAOYIENV_FILE'),
    ('os.path.expanduser("~/.openclaw/scripts/sync_claw_code.sh")', 'path_resolver.SYNC_CLAW_SCRIPT'),
    ('os.path.expanduser("~/.openclaw/extensions/memory-tencentdb/node_modules/sqlite-vec-linux-x64/vec0.so")', 'path_resolver.SQLITE_VEC_TENCENTDB'),
    ('os.path.expanduser("~/.openclaw/node_modules/sqlite-vec-linux-x64/vec0.so")', 'path_resolver.SQLITE_VEC_NODE'),

    # --- Longer skill-specific expanduser paths ---
    ('os.path.expanduser("~/.openclaw/workspace/skills/llm-memory-integration/config/llm_config.json")', 'path_resolver.LLM_CONFIG_JSON'),
    ('os.path.expanduser("~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core")', 'path_resolver.XIAOYI_OMEGA_LLM_CORE'),
    ('os.path.expanduser("~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/config/llm_config.json")', 'path_resolver.XIAOYI_OMEGA_LLM_CONFIG'),

    # --- String literals in code (NOT inside expanduser) ---
    ('"~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"', 'str(path_resolver.XIAOYI_OMEGA_LLM_CORE)'),
    ('"~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/config/llm_config.json"', 'str(path_resolver.XIAOYI_OMEGA_LLM_CONFIG)'),
    ('"~/.openclaw/workspace/skills/llm-memory-integration/config/llm_config.example.json"', 'str(path_resolver.LLM_CONFIG_EXAMPLE)'),
    ('"~/.openclaw/workspace/skills/llm-memory-integration/config/llm_config.json"', 'str(path_resolver.LLM_CONFIG_JSON)'),
    ('"~/.openclaw/workspace/skills/xiaoyi-web-search/scripts/search.js"', 'str(path_resolver.XIAOYI_WEB_SEARCH_SCRIPT)'),
    ('"~/.openclaw/workspace/skills/seedream-image_gen/scripts/generate_seedream.py"', 'str(path_resolver.SEEDREAM_SCRIPT)'),
    ('"~/.openclaw/workspace/GalaxyOS/models/embeddings"', 'str(path_resolver.GALAXYOS_EMBEDDINGS)'),
    ('"~/.openclaw/workspace/GalaxyOS/data/capability_registry"', 'str(path_resolver.GALAXYOS_CAPABILITY)'),
    ('"~/.openclaw/workspace/generated-images"', 'str(path_resolver.GENERATED_IMAGES)'),
    ('"~/.openclaw/workspace/.neural_cache"', 'str(path_resolver.NEURAL_CACHE_DIR)'),
    ('"~/.openclaw/workspace/repo/lib/python3.12/site-packages/sqlite_vec/vec0.so"', 'str(path_resolver.SQLITE_VEC_PY312)'),
    ('"~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts"', 'str(path_resolver.XIAOYI_OMEGA_SCRIPTS)'),
    ('"~/.openclaw/workspace/skills/llm-memory-integration/scripts"', 'str(path_resolver.LLM_MEMORY_SCRIPTS)'),
    ('"~/.openclaw/workspace/skills/llm-memory-integration"', 'str(path_resolver.LLM_MEMORY_DIR)'),
    ('"~/.openclaw/workspace/skills/llm-memory-integration/src/privileged"', 'str(path_resolver.LLM_MEMORY_PRIVILEGED)'),

    # --- Shell-command strings ---
    ('$OPENCLAW_HOME/workspace/skills/llm-memory-integration/config/llm_config.example.json', 'str(path_resolver.LLM_CONFIG_EXAMPLE)'),

    # --- Path.home() patterns (only specific well-known ones) ---
    ('Path.home() / ".openclaw/workspace/skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"', 'path_resolver.XIAOYI_OMEGA_LLM_CORE'),
    ('Path.home() / ".openclaw/workspace/skills/llm-memory-integration/src/privileged"', 'path_resolver.LLM_MEMORY_PRIVILEGED'),
    ('Path.home() / ".openclaw/workspace/skills"', 'path_resolver.SKILLS_DIR'),
    ('Path.home() / ".openclaw/memory-tdai/config/extension_config.json"', 'path_resolver.MEMORY_TDAI_CONFIG'),
    ('Path.home() / ".openclaw/memory-tdai/vectors.db"', 'path_resolver.VECTORS_DB'),
    ('Path.home() / ".openclaw/kora_behavior.db"', 'path_resolver.KORA_BEHAVIOR_DB'),
    ('Path.home() / ".openclaw/logs/audit"', 'path_resolver.AUDIT_LOG_DIR'),
]


def find_insert_pos(content: str) -> int:
    """Find where to insert the path_resolver setup code.
    After shebang, docstring, and standard library imports.
    """
    lines = content.split('\n')
    i = 0
    # Skip shebang
    if lines[i].startswith('#!'):
        i += 1
    # Skip docstring ("""...""" or '''...''')
    if i < len(lines) and (lines[i].lstrip().startswith('"""') or lines[i].lstrip().startswith("'''")):
        if lines[i].count('"""') >= 2 or lines[i].count("'''") >= 2:
            i += 1  # single-line docstring
        else:
            i += 1
            while i < len(lines) and '"""' not in lines[i] and "'''" not in lines[i]:
                i += 1
            i += 1  # skip closing """
    # Now find the first non-import, non-comment, non-blank line
    # and insert BEFORE it (so path_resolver is available early)
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith('#'):
            i += 1
        elif stripped.startswith('import ') or stripped.startswith('from '):
            i += 1
        else:
            break

    # Insert at position i (before the first real code)
    return i


def process_file(filepath: str) -> int:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return 0

    original = content
    changes = 0

    for old, new in REPLACE:
        count = content.count(old)
        if count > 0:
            content = content.replace(old, new)
            changes += count

    if changes == 0 or content == original:
        return 0

    # Only add import if not already there AND we replaced paths
    if 'import core.path_resolver' not in content:
        pos = find_insert_pos(content)
        lines = content.split('\n')
        lines.insert(pos, PATH_SETUP.rstrip('\n'))
        content = '\n'.join(lines)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  ✓ {os.path.relpath(filepath, WORKSPACE)} ({changes} changes)")
    return changes


def main():
    total_changes = 0
    files_modified = 0

    for root, dirs, files in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fname in files:
            if not fname.endswith('.py'):
                continue
            if fname in ('path_resolver.py', '_refactor_paths.py'):
                continue
            fpath = os.path.join(root, fname)
            c = process_file(fpath)
            if c > 0:
                total_changes += c
                files_modified += 1

    print(f"\n{'='*60}")
    print(f"Done: {files_modified} files modified, {total_changes} total replacements")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
