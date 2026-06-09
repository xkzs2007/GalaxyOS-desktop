#!/usr/bin/env python3
"""Pass 3: Clean up redundant os.path.expanduser wrappers left from pass 2,
   and handle remaining multi-line string patterns."""
import os, re

WORKSPACE = "/workspace"
SKIP = {"node_modules", ".git", "dist", "__pycache__"}

# Cleanup patterns: os.path.expanduser wrapping a path_resolver constant
CLEANUP_REPLACE = [
    # Pattern: os.path.expanduser(\n    str(path_resolver.XXX)\n) → path_resolver.XXX
    # --- XIAOYI_OMEGA_LLM_CONFIG ---
    (
        r'os\.path\.expanduser\(\s*\n\s*str\(path_resolver\.XIAOYI_OMEGA_LLM_CONFIG\)\s*\n\s*\)',
        'path_resolver.XIAOYI_OMEGA_LLM_CONFIG'
    ),
    (
        r'os\.path\.expanduser\(\s*str\(path_resolver\.XIAOYI_OMEGA_LLM_CONFIG\)\s*\)',
        'path_resolver.XIAOYI_OMEGA_LLM_CONFIG'
    ),
    # --- GALAXYOS_CAPABILITY ---
    (
        r'os\.path\.expanduser\(\s*\n\s*str\(path_resolver\.GALAXYOS_CAPABILITY\)\s*\n\s*\)',
        'path_resolver.GALAXYOS_CAPABILITY'
    ),
    # --- GALAXYOS_EMBEDDINGS ---
    (
        r'os\.path\.expanduser\(\s*\n\s*str\(path_resolver\.GALAXYOS_EMBEDDINGS\)\s*\n\s*\)',
        'path_resolver.GALAXYOS_EMBEDDINGS'
    ),
    # --- NEURAL_CACHE_DIR ---
    (
        r'os\.path\.expanduser\(\s*\n\s*str\(path_resolver\.NEURAL_CACHE_DIR\)\s*\n\s*\)',
        'path_resolver.NEURAL_CACHE_DIR'
    ),
    # --- SQLITE_VEC_PY312 ---
    (
        r'os\.path\.expanduser\(\s*\n\s*str\(path_resolver\.SQLITE_VEC_PY312\)\s*\n\s*\)',
        'path_resolver.SQLITE_VEC_PY312'
    ),
    # --- XIAOYI_WEB_SEARCH_SCRIPT ---
    (
        r'os\.path\.expanduser\(\s*\n\s*str\(path_resolver\.XIAOYI_WEB_SEARCH_SCRIPT\)\s*\n\s*\)',
        'path_resolver.XIAOYI_WEB_SEARCH_SCRIPT'
    ),
    (
        r'os\.path\.expanduser\(\s*str\(path_resolver\.XIAOYI_WEB_SEARCH_SCRIPT\)\s*\)',
        'path_resolver.XIAOYI_WEB_SEARCH_SCRIPT'
    ),
    # --- SEEDREAM_SCRIPT ---
    (
        r'os\.path\.expanduser\(\s*\n\s*str\(path_resolver\.SEEDREAM_SCRIPT\)\s*\n\s*\)',
        'path_resolver.SEEDREAM_SCRIPT'
    ),
    # --- GENERATED_IMAGES ---
    (
        r'os\.path\.expanduser\(\s*\n\s*str\(path_resolver\.GENERATED_IMAGES\)\s*\n\s*\)',
        'path_resolver.GENERATED_IMAGES'
    ),
]

# Remaining bare-string multi-line expanduser calls not yet handled
BARESTRING_REPLACE = [
    # claw_helpers.py: _rci_mmap = os.path.expanduser(\n    "~/.openclaw/.../rci_shared_state"\n)
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/extensions/claw-core/var/rci_shared_state"\s*\n\s*\)',
        'path_resolver.RCI_SHARED_STATE'
    ),
    # xiaoyi_claw_api.py / services/xiaoyi_claw_api.py:
    # _neural_data_dir = os.path.expanduser(\n    "~/.openclaw/.../synapse_network")
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/\.learnings/synapse_network"\s*\n\s*\)',
        'path_resolver.SYNAPSE_NETWORK'
    ),
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/\.learnings/synapse_network"\)',
        'path_resolver.SYNAPSE_NETWORK'
    ),
    # capability_registry.py: data_dir or os.path.expanduser(\n    "~/.openclaw/.../capability_registry")
    # These were already partially converted, but let me handle any remaining
    (
        r'os\.path\.expanduser\(\s*\n\s*"~/.openclaw/workspace/GalaxyOS/data/capability_registry"\s*\n\s*\)',
        'path_resolver.GALAXYOS_CAPABILITY'
    ),
]


def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    original = content
    changes = 0

    for pattern, replacement in CLEANUP_REPLACE + BARESTRING_REPLACE:
        new_content, n = re.subn(pattern, replacement, content, flags=re.MULTILINE)
        if n > 0:
            content = new_content
            changes += n

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"  ✓ {os.path.relpath(filepath, WORKSPACE)} ({changes} fixes)")

    return changes


def main():
    total = 0
    files = 0

    for root, dirs, fnames in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fname in fnames:
            if not fname.endswith('.py'):
                continue
            if fname in ('path_resolver.py', '_refactor_paths.py', '_refactor_pass2.py'):
                continue
            fpath = os.path.join(root, fname)
            c = process_file(fpath)
            if c > 0:
                total += c
                files += 1

    print(f"\nPass 3 done: {files} files cleaned, {total} fixes")


if __name__ == '__main__':
    main()
