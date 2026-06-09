#!/usr/bin/env python3
"""Fix _path_resolver references:
  1. In PATH_SETUP blocks: _os.path.expanduser("~/.openclaw/workspace") → _os.path.expanduser("~/.openclaw/workspace")
  2. In code: _path_resolver.XXX → path_resolver.XXX
"""
import os

WORKSPACE = "/workspace"
SKIP = {"node_modules", ".git", "dist", "__pycache__"}


def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    if '_path_resolver' not in content:
        return False

    original = content
    changes = 0

    # Fix 1: PATH_SETUP fallback: _os.path.expanduser("~/.openclaw/workspace") → _os.path.expanduser("~/.openclaw/workspace")
    old = '_os.path.expanduser("~/.openclaw/workspace")'
    new = '_os.path.expanduser("~/.openclaw/workspace")'
    count = content.count(old)
    if count > 0:
        content = content.replace(old, new)
        changes += count

    # Fix 2: path_resolver.TEMPORAL_KG_DB → path_resolver.TEMPORAL_KG_DB
    old = 'path_resolver.TEMPORAL_KG_DB'
    new = 'path_resolver.TEMPORAL_KG_DB'
    count = content.count(old)
    if count > 0:
        content = content.replace(old, new)
        changes += count

    # Fix 3: path_resolver.RCI_SHARED_STATE → path_resolver.RCI_SHARED_STATE
    old = 'path_resolver.RCI_SHARED_STATE'
    new = 'path_resolver.RCI_SHARED_STATE'
    count = content.count(old)
    if count > 0:
        content = content.replace(old, new)
        changes += count

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"  ✓ {os.path.relpath(filepath, WORKSPACE)} ({changes} fixes)")
        return True
    return False


def main():
    count = 0
    for root, dirs, files in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fname in files:
            if not fname.endswith('.py'):
                continue
            if '_refactor' in fname:
                continue
            fpath = os.path.join(root, fname)
            if process_file(fpath):
                count += 1
    print(f"\nFixed {count} files")


if __name__ == '__main__':
    main()
