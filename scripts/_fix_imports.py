#!/usr/bin/env python3
"""Fix: add path_resolver import to files that use it but don't import it."""
import os

WORKSPACE = "/workspace"
SKIP = {"node_modules", ".git", "dist", "__pycache__"}

IMPORT_BLOCK = """
# ── Centralized path resolution ──
import os as _os, sys as _sys
_ws_root = _os.environ.get("OPENCLAW_WORKSPACE", _os.path.expanduser("~/.openclaw/workspace"))
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
"""


def add_import(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    if 'import core.path_resolver' in content:
        return False

    lines = content.split('\n')
    i = 0

    # Skip shebang
    if lines[i].startswith('#!'):
        i += 1

    # Skip docstring
    stripped = lines[i].strip() if i < len(lines) else ''
    if stripped.startswith('"""') or stripped.startswith("'''"):
        if stripped.count(stripped[:3]) >= 2:
            i += 1
        else:
            i += 1
            while i < len(lines) and stripped[:3] not in lines[i]:
                i += 1
            i += 1

    # Find insertion point: before the first non-import, non-comment code
    # Skip all import lines and blank/comment lines
    first_code = i
    for j in range(i, len(lines)):
        s = lines[j].strip()
        if not s or s.startswith('#'):
            continue
        if s.startswith('import ') or s.startswith('from '):
            # Check if this is a multi-line import
            if '(' in s and ')' not in s:
                # Multi-line import - skip until closing paren
                k = j + 1
                while k < len(lines) and ')' not in lines[k]:
                    k += 1
                j = k  # skip past the multi-line import
            continue
        first_code = j
        break

    # Insert before first_code
    lines.insert(first_code, IMPORT_BLOCK.rstrip('\n'))
    content = '\n'.join(lines)

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"  + import: {os.path.relpath(filepath, WORKSPACE)}")
    return True


def main():
    count = 0
    for root, dirs, files in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fname in files:
            if not fname.endswith('.py'):
                continue
            if 'path_resolver' in fname or '_refactor' in fname:
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, 'r') as f:
                content = f.read()
            if 'path_resolver.' in content and 'import core.path_resolver' not in content:
                if add_import(fpath):
                    count += 1
    print(f"\nAdded import to {count} files")


if __name__ == '__main__':
    main()
