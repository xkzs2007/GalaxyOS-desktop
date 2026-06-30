# -*- mode: python ; coding: utf-8 -*-
# galaxyos-sidecar.spec — PyInstaller spec for GalaxyOS desktop sidecar.
#
# This bundles the Python sidecar (`galaxyos_sidecar.py`) and its
# siblings in `desktop-shell/python/` together with the full
# `galaxyos/` engine package into a single executable:
#
#   - Windows  → desktop-shell/python/dist/galaxyos-sidecar.exe
#   - Linux    → desktop-shell/python/dist/galaxyos-sidecar
#   - macOS    → desktop-shell/python/dist/galaxyos-sidecar
#
# The Electron main process (src/main.ts) detects the platform and
# appends `.exe` on Windows via `resolveSidecarPath()`.
#
# Build (cross-platform):
#   cd desktop-shell/python
#   pyinstaller --clean --noconfirm galaxyos-sidecar.spec
#
# The spec must be run from inside `desktop-shell/python/` (the
# relative `datas` / `pathex` entries assume that CWD).

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# ── Sidecar sibling modules (live next to galaxyos_sidecar.py) ─────────
# PyInstaller's static analysis can't always see runtime imports of
# modules in the same directory, so we list them explicitly.
sibling_modules = [
    'path_resolver_desktop',
    'tokui_dsl',
    'llm_providers',
    'agent_loop',
    'memo_stages',
    'memo_adapter',
    'executive_client',
    'ac_router',
    'tools',
    'mcp_client',
    'skill_graph',
    'galaxy_agent',
    'cumulative_regret',
]

# ── GalaxyOS engine submodules (transitive imports) ────────────────────
hiddenimports = list(sibling_modules)
hiddenimports += collect_submodules('galaxyos.engine')
hiddenimports += collect_submodules('galaxyos.privileged')
hiddenimports += collect_submodules('galaxyos.shared')
hiddenimports += collect_submodules('galaxyos.orchestration')
# Engine uses some bare imports of upstream OpenClaw-era modules
# (e.g. `from unified_vector_store import ...`). The desktop shim
# re-roots them via `path_resolver_desktop`, so we don't need to
# collect the legacy `path_resolver` itself — only the shim.

# ── Datas ───────────────────────────────────────────────────────────────
# Two types:
#   1. PyInstaller metadata for sibling modules with package_data
#      (e.g. skills, jieba dicts) — collected automatically.
#   2. The whole `galaxyos/` package directory bundled as
#      `galaxyos/` inside the archive, so `from galaxyos.engine...`
#      resolves at runtime inside the frozen app.
#
# CRITICAL: `datas` must be declared ONCE. A previous version of
# this spec had `datas = []` twice and the second overwrote the
# first — causing the `galaxyos/` package to be silently dropped
# from the bundle and `ModuleNotFoundError: galaxyos` at runtime.
datas = []
# GalaxyOS engine package. Layout (verified against this repo):
#   <repo>/desktop-shell/python/galaxyos-sidecar.spec   ← this spec
#   <repo>/desktop-shell/python/                        ← spec CWD
#   <repo>/galaxyos/                                    ← the Python package itself
#                                                          (has __init__.py at the root,
#                                                           plus engine/ privileged/ etc.
#                                                           as subpackages)
#
# PyInstaller `datas` tuple is `(source, dest)`:
#   - source: a file or directory on the build host
#   - dest:   the relative path inside `_MEIPASS`
#
# We want the entire `galaxyos/` Python package (with all its
# subpackages and any package_data) to land at `_MEIPASS/galaxyos/`
# so that at runtime `import galaxyos.engine.xxx` resolves
# naturally — `sys.path` already contains _MEIPASS at the front
# in PyInstaller onefile mode.
import os
_spec_dir = os.path.abspath(os.path.dirname(SPEC))  # noqa: F821
# _galaxyos_pkg = <repo>/galaxyos  (the Python package itself,
# containing __init__.py at the top level)
_galaxyos_pkg = os.path.abspath(os.path.join(_spec_dir, '..', '..', 'galaxyos'))
# Sanity: the source path must actually exist. If the spec was run
# from a weird CWD, the abs-path computation still gives the right
# result; if galaxyos/ has been moved, fail loudly here instead of
# silently producing a bundle that crashes at runtime.
assert os.path.isfile(os.path.join(_galaxyos_pkg, '__init__.py')), (
    f"galaxyos package not found at {_galaxyos_pkg} "
    f"(no __init__.py). spec_dir={_spec_dir}. "
    f"This usually means the spec is being run from the wrong CWD "
    f"or the repo layout has changed."
)
# Bundle the entire `galaxyos/` package (and any subpackages)
# under `_MEIPASS/galaxyos/`. PyInstaller recursively copies the
# tree; at runtime `import galaxyos.engine.xxx` resolves to
# `_MEIPASS/galaxyos/engine/xxx.py`.
datas += [
    (_galaxyos_pkg, 'galaxyos'),
]
# Pull in any package_data for sibling modules (skills, jieba
# dicts, etc.). Safe to call even if the module isn't installed as
# a package — `collect_data_files` returns [] in that case.
for mod in sibling_modules:
    try:
        datas += collect_data_files(mod)
    except Exception:
        # Module not installed as a package; PyInstaller still picks
        # up the .py file via hiddenimports.
        pass
# Belt-and-suspenders: also collect_data_files for the galaxyos
# package itself, in case any subpackage has a MANIFEST.in that
# copies extra data (skills, prompts, schema files).
for sub in ('galaxyos', 'galaxyos.engine', 'galaxyos.privileged',
            'galaxyos.shared', 'galaxyos.orchestration', 'galaxyos.harness'):
    try:
        datas += collect_data_files(sub)
    except Exception:
        pass

# ── Analysis ────────────────────────────────────────────────────────────
a = Analysis(
    ['galaxyos_sidecar.py'],
    pathex=['.', '../../', '../../galaxyos/engine', '../../galaxyos/privileged'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy ML deps the sidecar never imports (we use Mock backends
        # in the packaged build). Trimming them cuts the binary by
        # ~80 MB on Windows.
        'torch',
        'torchvision',
        'transformers',
        'onnxruntime',
        'faiss',
        'hnswlib',
        'pandas',
        'mkl',
        'tbb',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='galaxyos-sidecar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX often triggers antivirus false positives on Windows
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
