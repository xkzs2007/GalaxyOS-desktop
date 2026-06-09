#!/usr/bin/env python3
"""Pass 4: Handle multi-segment Path.home() patterns.

Strategy:
  Step A: Path.home() / ".openclaw" → path_resolver.OPENCLAW_HOME
  Step B: path_resolver.OPENCLAW_HOME / "workspace" → path_resolver.WORKSPACE_ROOT
  Step C: path_resolver.OPENCLAW_HOME / "memory-tdai" → path_resolver.MEMORY_TDAI_DIR
  Step D: path_resolver.OPENCLAW_HOME / "openclaw.json" → path_resolver.OPENCLAW_CONFIG
  Step E: path_resolver.OPENCLAW_HOME / "extensions" → path_resolver.EXTENSIONS_DIR
  Step F: path_resolver.OPENCLAW_HOME / "skills" / "llm-memory-integration" → path_resolver.LLM_MEMORY_DIR
"""
import os, re

WORKSPACE = "/workspace"
SKIP = {"node_modules", ".git", "dist", "__pycache__"}

REPLACE_STEPS = [
    # Step A: Path.home() / ".openclaw" → path_resolver.OPENCLAW_HOME
    (r'Path\.home\(\)\s*/\s*"\.openclaw"', 'path_resolver.OPENCLAW_HOME'),
    # Step B: path_resolver.OPENCLAW_HOME / "workspace" → path_resolver.WORKSPACE_ROOT
    (r'path_resolver\.OPENCLAW_HOME\s*/\s*"workspace"', 'path_resolver.WORKSPACE_ROOT'),
    # Step C: path_resolver.OPENCLAW_HOME / "memory-tdai" → path_resolver.MEMORY_TDAI_DIR
    (r'path_resolver\.OPENCLAW_HOME\s*/\s*"memory-tdai"', 'path_resolver.MEMORY_TDAI_DIR'),
    # Step D: path_resolver.OPENCLAW_HOME / "openclaw.json" → path_resolver.OPENCLAW_CONFIG
    (r'path_resolver\.OPENCLAW_HOME\s*/\s*"openclaw\.json"', 'path_resolver.OPENCLAW_CONFIG'),
    # Step E: path_resolver.OPENCLAW_HOME / "extensions" → path_resolver.EXTENSIONS_DIR
    (r'path_resolver\.OPENCLAW_HOME\s*/\s*"extensions"', 'path_resolver.EXTENSIONS_DIR'),
    # Step F: path_resolver.OPENCLAW_HOME / "skills" / "llm-memory-integration"
    (r'(path_resolver\.WORKSPACE_ROOT|path_resolver\.OPENCLAW_HOME)\s*/\s*"skills"\s*/\s*"llm-memory-integration"', 'path_resolver.LLM_MEMORY_DIR'),
    # Step G: path_resolver.OPENCLAW_HOME / "memory-tdai" / ".cache" (already mapped to MEMORY_TDAI_DIR, then / ".cache")
    # This is handled by step C first, resulting in path_resolver.MEMORY_TDAI_DIR / ".cache"
    # Step H: path_resolver.OPENCLAW_HOME / "ontology" → not in resolver, leave
]

# Files with .openclaw-memory (different root, keep as-is)


def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Skip files that don't have Path.home()
    if 'Path.home()' not in content:
        return 0

    original = content
    changes = 0

    for pattern, replacement in REPLACE_STEPS:
        new_content, n = re.subn(pattern, replacement, content)
        if n > 0:
            content = new_content
            changes += n

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"  ✓ {os.path.relpath(filepath, WORKSPACE)} ({changes} replacements)")

    return changes


def main():
    total = 0
    files = 0

    for root, dirs, fnames in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fname in fnames:
            if not fname.endswith('.py'):
                continue
            if 'path_resolver' in fname or '_refactor' in fname:
                continue
            fpath = os.path.join(root, fname)
            c = process_file(fpath)
            if c > 0:
                total += c
                files += 1

    print(f"\nPass 4 done: {files} files updated, {total} replacements")


if __name__ == '__main__':
    main()
