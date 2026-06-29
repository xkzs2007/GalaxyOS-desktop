"""desktop_shell_compat — Bridge layer for the harness.

The harness layer (galaxyos.harness.*) lives in the **core** package
and should NOT depend on desktop-shell directly. This shim bridges
desktop-shell internals (tools, vector_store, skill_graph, etc.) into
the harness namespace.

Usage (recommended):
    from galaxyos.harness.desktop_shell_compat import tools
    from galaxyos.harness.desktop_shell_compat import vector_store

Usage (works but slower — first import auto-registers):
    import importlib
    tools = importlib.import_module("galaxyos.harness.desktop_shell_compat.tools")

Implementation:
    We use Python's importlib finder protocol: register a custom
    MetaPathFinder that intercepts imports of
    ``galaxyos.harness.desktop_shell_compat.<X>`` and serves the
    real module from desktop-shell/python/ or galaxyos/privileged/.
"""
from __future__ import annotations

import logging
import os
import sys
import importlib
import importlib.abc
import importlib.machinery
from pathlib import Path

log = logging.getLogger("galaxyos.harness.compat")

# Locate desktop-shell/python and galaxyos sub-packages
# desktop_shell_compat/__init__.py is at:
#   /workspace/galaxyos/harness/desktop_shell_compat/__init__.py
# So _REPO_ROOT (workspace) is parent.parent.parent
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent.parent  # /workspace
_DESKTOP_PYTHON = _REPO_ROOT / "desktop-shell" / "python"
_GALAXYOS_PKG = _REPO_ROOT / "galaxyos"
_GALAXYOS_PRIVILEGED = _GALAXYOS_PKG / "privileged"
_GALAXYOS_ENGINE = _GALAXYOS_PKG / "engine"

# Prepend paths so target modules can be imported as top-level names
for d in (_DESKTOP_PYTHON, _GALAXYOS_PRIVILEGED, _GALAXYOS_ENGINE,
          _GALAXYOS_PKG, _REPO_ROOT):
    d_str = str(d)
    if d.exists() and d_str not in sys.path:
        sys.path.insert(0, d_str)


# Map of public sub-module names → top-level module path to load from
_BRIDGE_MODULES = {
    "tools": "tools",
    "agent_loop": "agent_loop",
    "ac_router": "ac_router",
    "memo_adapter": "memo_adapter",
    "executive_client": "executive_client",
    "memo_stages": "memo_stages",
    "path_resolver_desktop": "path_resolver_desktop",
    "skill_graph": "skill_graph",     # copied to desktop-shell/python
    "vector_store": "vector_store",   # galaxyos/privileged/
}


def _load_bridge(name: str):
    """Load a bridged module by name, caching under the full harness path."""
    if name not in _BRIDGE_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    target = _BRIDGE_MODULES[name]
    full = f"galaxyos.harness.desktop_shell_compat.{name}"
    # Ensure all source paths are on sys.path BEFORE importing
    for d in (_DESKTOP_PYTHON, _GALAXYOS_PRIVILEGED, _GALAXYOS_ENGINE,
              _GALAXYOS_PKG, _REPO_ROOT):
        d_str = str(d)
        if d.exists() and d_str not in sys.path:
            sys.path.insert(0, d_str)
    try:
        mod = importlib.import_module(target)
    except ImportError as e:
        raise ImportError(
            f"desktop_shell_compat: cannot import {name!r} "
            f"(target: {target!r}): {e}"
        ) from e
    sys.modules[full] = mod
    return mod


# ── Module attribute access (PEP 562) ──────────────────────────────
# `from galaxyos.harness.desktop_shell_compat import tools`
def __getattr__(name):
    return _load_bridge(name)


# ── MetaPathFinder for direct importlib use ────────────────────────
class _BridgeFinder(importlib.abc.MetaPathFinder):
    """Intercept imports of galaxyos.harness.desktop_shell_compat.<X>."""
    def find_spec(self, fullname, path, target=None):
        prefix = "galaxyos.harness.desktop_shell_compat."
        if not fullname.startswith(prefix):
            return None
        sub = fullname[len(prefix):]
        if sub not in _BRIDGE_MODULES:
            return None
        # Serve the already-imported module from sys.modules if cached
        if fullname in sys.modules:
            return importlib.util.spec_from_loader(
                fullname, importlib.machinery.SourceFileLoader(
                    fullname, sys.modules[fullname].__file__,
                )
            )
        # Otherwise load it now
        try:
            mod = _load_bridge(sub)
        except ImportError:
            return None
        return importlib.util.spec_from_loader(
            fullname, importlib.machinery.SourceFileLoader(
                fullname, mod.__file__,
            )
        )


# Register the finder (only once)
_finder_id = f"_GalaxyosBridgeFinder_{id(_BRIDGE_MODULES)}"
if not any(getattr(f, "_galaxyos_bridge", False) for f in sys.meta_path):
    _finder = _BridgeFinder()
    _finder._galaxyos_bridge = True  # type: ignore[attr-defined]
    sys.meta_path.insert(0, _finder)


__all__ = list(_BRIDGE_MODULES.keys())
