#!/usr/bin/env python3
"""Second pass: fix remaining expanduser/Path.home patterns after initial refactoring.

Handles:
  1. Multi-line expanduser calls (split across lines)
  2. Single-quote variants not in the original replace map
  3. Remaining Path.home() patterns
"""
import os, re, sys

import os as _os, sys as _sys
_ws_root = _os.environ.get("OPENCLAW_WORKSPACE", _os.path.expanduser("~/.openclaw/workspace"))
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
WORKSPACE = "/workspace"
SKIP = {"node_modules", ".git", "dist", "__pycache__"}

# Multi-line patterns to replace: (regex, replacement)
# These match expanduser( + newline + optional whitespace + string
MULTILINE_REPLACE = [
    # XIAOYI_OMEGA_SCRIPTS
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/scripts"\)',
        'path_resolver.XIAOYI_OMEGA_SCRIPTS'
    ),
    # XIAOYI_OMEGA_LLM_CONFIG (double quote, multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/config/llm_config\.json"\)',
        'path_resolver.XIAOYI_OMEGA_LLM_CONFIG'
    ),
    # XIAOYI_OMEGA_LLM_CONFIG (single quote, multiline)
    (
        r"os\.path\.expanduser\(\s*\n\s*'~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/config/llm_config\.json'\)",
        'path_resolver.XIAOYI_OMEGA_LLM_CONFIG'
    ),
    # XIAOYI_WEB_SEARCH_SCRIPT (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/skills/xiaoyi-web-search/scripts/search\.js"\)',
        'path_resolver.XIAOYI_WEB_SEARCH_SCRIPT'
    ),
    # GALAXYOS_CAPABILITY (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*str(path_resolver.GALAXYOS_CAPABILITY)\)',
        'path_resolver.GALAXYOS_CAPABILITY'
    ),
    # SKILLS_DIR (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/skills"\)',
        'path_resolver.SKILLS_DIR'
    ),
    # LLM_CONFIG_JSON (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/skills/llm-memory-integration/config/llm_config\.json"\)',
        'path_resolver.LLM_CONFIG_JSON'
    ),
    # GENERATED_IMAGES (multiline, single quote)
    (
        r"os\.path\.expanduser\(\s*\n\s*'~/.openclaw/workspace/generated-images'\)",
        'path_resolver.GENERATED_IMAGES'
    ),
    # SEEDREAM_SCRIPT (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/skills/seedream-image_gen/scripts/generate_seedream\.py"\)',
        'path_resolver.SEEDREAM_SCRIPT'
    ),
    # GALAXYOS_EMBEDDINGS (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*str(path_resolver.GALAXYOS_EMBEDDINGS)\)',
        'path_resolver.GALAXYOS_EMBEDDINGS'
    ),
    # NEURAL_CACHE_DIR (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/\.neural_cache"\)',
        'path_resolver.NEURAL_CACHE_DIR'
    ),
    # SQLITE_VEC_PY312 (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/repo/lib/python3\.12/site-packages/sqlite_vec/vec0\.so"\)',
        'path_resolver.SQLITE_VEC_PY312'
    ),
    # DAG_BLOB_ARENA (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/dag_blob_arena"\)',
        'path_resolver.DAG_BLOB_ARENA'
    ),
    # WORKSPACE_ROOT (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace"\)',
        'path_resolver.WORKSPACE_ROOT'
    ),
    # XIAOYI_OMEGA_LLM_CORE (multiline)
    (
        r'os\.path\.expanduser\(\s*\n\s*str(path_resolver.XIAOYI_OMEGA_LLM_CORE)\)',
        'path_resolver.XIAOYI_OMEGA_LLM_CORE'
    ),
]

# Single-line patterns missed in first pass (mostly single-quote variants)
SINGLE_LINE_REPLACE = [
    # XIAOYI_OMEGA_LLM_CONFIG (single quote)
    (
        "path_resolver.XIAOYI_OMEGA_LLM_CONFIG",
        "path_resolver.XIAOYI_OMEGA_LLM_CONFIG"
    ),
    # XIAOYI_OMEGA_SCRIPTS (single quote)  
    (
        "path_resolver.XIAOYI_OMEGA_SCRIPTS",
        "path_resolver.XIAOYI_OMEGA_SCRIPTS"
    ),
    # XIAOYI_WEB_SEARCH_SCRIPT (single quote)
    (
        "path_resolver.XIAOYI_WEB_SEARCH_SCRIPT",
        "path_resolver.XIAOYI_WEB_SEARCH_SCRIPT"
    ),
    # LLM_MEMORY_SCRIPTS (single quote)
    (
        "path_resolver.LLM_MEMORY_SCRIPTS",
        "path_resolver.LLM_MEMORY_SCRIPTS"
    ),
    # LLM_MEMORY_DIR (single quote)
    (
        "path_resolver.LLM_MEMORY_DIR",
        "path_resolver.LLM_MEMORY_DIR"
    ),
    # SEEDREAM_SCRIPT (single quote)
    (
        "path_resolver.SEEDREAM_SCRIPT",
        "path_resolver.SEEDREAM_SCRIPT"
    ),
    # GALAXYOS_EMBEDDINGS (single quote)
    (
        "path_resolver.GALAXYOS_EMBEDDINGS",
        "path_resolver.GALAXYOS_EMBEDDINGS"
    ),
    # NEURAL_CACHE_DIR (single quote)
    (
        "path_resolver.NEURAL_CACHE_DIR",
        "path_resolver.NEURAL_CACHE_DIR"
    ),
    # SQLITE_VEC_PY312 (single quote)
    (
        "path_resolver.SQLITE_VEC_PY312",
        "path_resolver.SQLITE_VEC_PY312"
    ),
    # XIAOYI_OMEGA_LLM_CORE (single quote)
    (
        "path_resolver.XIAOYI_OMEGA_LLM_CORE",
        "path_resolver.XIAOYI_OMEGA_LLM_CORE"
    ),
    # LLM_CONFIG_JSON (single quote) - already in map, but double check
    (
        "path_resolver.LLM_CONFIG_JSON",
        "path_resolver.LLM_CONFIG_JSON"
    ),
    # WORKSPACE_ROOT (single quote) - already in map, but double check
    (
        "path_resolver.WORKSPACE_ROOT",
        "path_resolver.WORKSPACE_ROOT"
    ),
]

# Remaining Path.home() patterns
PATHHOME_REPLACE = [
    # path_resolver.SKILLS_DIR (multi-part)
    (
        r'Path\.home\(\)\s*/\s*"\.openclaw"\s*/\s*"workspace"\s*/\s*"skills"',
        'path_resolver.SKILLS_DIR'
    ),
    # Path.home() / ".openclaw/..."
    (
        r'Path\.home\(\)\s*/\s*"\.openclaw"\s*/\s*"memory-tdai"\s*/\s*"vectors\.db"',
        'path_resolver.VECTORS_DB'
    ),
    (
        r'Path\.home\(\)\s*/\s*"\.openclaw"\s*/\s*"extensions"\s*/\s*"memory-tencentdb"\s*/\s*"node_modules"\s*/\s*"sqlite-vec-linux-x64"\s*/\s*"vec0\.so"',
        'path_resolver.SQLITE_VEC_TENCENTDB'
    ),
    (
        r'Path\.home\(\)\s*/\s*"\.openclaw"\s*/\s*"extensions"\s*/\s*"memory-tencentdb"\s*/\s*"node_modules"\s*/\s*"sqlite-vec-linux-x64"\s*/\s*"vec0"',
        'path_resolver.SQLITE_VEC_TENCENTDB'
    ),
    (
        r'Path\.home\(\)\s*/\s*"\.openclaw"\s*/\s*"kora_behavior\.db"',
        'path_resolver.KORA_BEHAVIOR_DB'
    ),
    # Path.home() / ".openclaw-memory" — keep as-is for now (different root concept)
]


def process_file(filepath):
    """Process one file with all replacement rules."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    original = content
    changes = 0
    
    # Multi-line regex replacements
    for pattern, replacement in MULTILINE_REPLACE:
        new_content, n = re.subn(pattern, replacement, content)
        if n > 0:
            content = new_content
            changes += n
    
    # Single-line string replacements
    for old, new in SINGLE_LINE_REPLACE:
        n = content.count(old)
        if n > 0:
            content = content.replace(old, new)
            changes += n
    
    # Path.home() regex replacements  
    for pattern, replacement in PATHHOME_REPLACE:
        new_content, n = re.subn(pattern, replacement, content)
        if n > 0:
            content = new_content
            changes += n
    
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"  ✓ {os.path.relpath(filepath, WORKSPACE)} ({changes} changes)")
    
    return changes


def main():
    total = 0
    files = 0
    
    for root, dirs, fnames in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fname in fnames:
            if not fname.endswith('.py'):
                continue
            if fname in ('path_resolver.py', '_refactor_paths.py'):
                continue
            fpath = os.path.join(root, fname)
            c = process_file(fpath)
            if c > 0:
                total += c
                files += 1
    
    print(f"\nPass 2 done: {files} more files updated, {total} additional replacements")


if __name__ == '__main__':
    main()
