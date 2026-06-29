"""conftest.py — pytest setup for the workspace root.

Adds desktop-shell/python to sys.path so tests can import the sidecar
modules (llm_providers, tokui_dsl, galaxyos_sidecar) without packaging.
Also enables pytest-asyncio plugin.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent
_SIDECAR_PY = _REPO / "desktop-shell" / "python"
for p in (str(_SIDECAR_PY), str(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import pytest_asyncio  # noqa: F401
except ImportError:
    pass
