#!/usr/bin/env bash
# scripts/build-python.sh — PyInstaller bundle for the sidecar.
#
# Cross-platform: works on Windows (Git Bash / WSL), macOS, Linux.
# Produces:
#   Windows → desktop-shell/python/dist/galaxyos-sidecar.exe
#   POSIX   → desktop-shell/python/dist/galaxyos-sidecar
#
# Usage:
#   bash scripts/build-python.sh              # auto-detect python
#   PYTHON=python3.11 bash scripts/build-python.sh
#   bash scripts/build-python.sh --clean      # nuke build/ first
#
# Exit codes:
#   0 — success
#   1 — pyinstaller / import failure
#   2 — python interpreter not found

set -euo pipefail

# ── Locate repo + interpreter ───────────────────────────────────────────
APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$APP_ROOT/.." && pwd)"
PYTHON_DIR="$APP_ROOT/python"

if [ "${1:-}" = "--clean" ]; then
    echo "[build-python] --clean: removing build/ and dist/"
    rm -rf "$PYTHON_DIR/build" "$PYTHON_DIR/dist"
fi

# Pick a Python: explicit override first, then common names.
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for cand in python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then
            PY="$(command -v "$cand")"
            break
        fi
    done
fi
if [ -z "$PY" ]; then
    echo "[build-python] ERROR: no Python interpreter found (set \$PYTHON)" >&2
    exit 2
fi

echo "[build-python] Python: $PY ($($PY --version 2>&1))"
echo "[build-python] Repo:   $REPO_ROOT"
echo "[build-python] CWD:    $PYTHON_DIR"

# ── Sanity-check: required modules importable ───────────────────────────
# We don't fail hard here (some modules might be in a separate venv),
# but we surface a clear error if PyInstaller itself is missing.
if ! "$PY" -c "import PyInstaller" 2>/dev/null; then
    echo "[build-python] Installing PyInstaller..."
    "$PY" -m pip install --quiet pyinstaller
fi

# ── Run PyInstaller with the spec ───────────────────────────────────────
cd "$PYTHON_DIR"

echo "[build-python] Bundling sidecar via PyInstaller..."
"$PY" -m PyInstaller \
    --clean \
    --noconfirm \
    galaxyos-sidecar.spec

# ── Verify + summarise ──────────────────────────────────────────────────
if [ -f dist/galaxyos-sidecar.exe ]; then
    OUT=dist/galaxyos-sidecar.exe
elif [ -f dist/galaxyos-sidecar ]; then
    OUT=dist/galaxyos-sidecar
    chmod +x "$OUT"
else
    echo "[build-python] ERROR: PyInstaller produced no output" >&2
    ls -la dist/ 2>/dev/null || true
    exit 1
fi

SIZE=$(du -h "$OUT" | cut -f1)
echo "[build-python] ✓ Done: $PYTHON_DIR/$OUT ($SIZE)"
