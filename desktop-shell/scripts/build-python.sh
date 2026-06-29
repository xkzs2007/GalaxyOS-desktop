#!/usr/bin/env bash
# scripts/build-python.sh — PyInstaller bundle for the sidecar.
# Produces desktop-shell/python/dist/galaxyos-sidecar (or .exe on Windows).

set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_DIR="$APP_ROOT/python"
VENV_PY="$APP_ROOT/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$APP_ROOT/.venv/Scripts/python.exe"

cd "$PYTHON_DIR"

echo "[build-python] Bundling sidecar via PyInstaller..."
"$VENV_PY" -m PyInstaller \
  --name galaxyos-sidecar \
  --onefile \
  --clean \
  --noconfirm \
  --paths "$PYTHON_DIR" \
  --collect-submodules galaxyos.engine \
  --collect-submodules galaxyos.privileged \
  --collect-submodules galaxyos.shared \
  --hidden-import path_resolver_desktop \
  --copy-metadata path_resolver_desktop \
  galaxyos_sidecar.py

echo "[build-python] Done. Output at $PYTHON_DIR/dist/galaxyos-sidecar"
