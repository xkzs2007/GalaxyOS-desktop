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
# `collect_submodules` follows `__init__.py` imports to discover every
# submodule of a package. We do this for each top-level GalaxyOS
# subpackage so PyInstaller's static analysis finds them all.
hiddenimports = list(sibling_modules)
hiddenimports += collect_submodules('galaxyos.engine')
hiddenimports += collect_submodules('galaxyos.privileged')
hiddenimports += collect_submodules('galaxyos.shared')
hiddenimports += collect_submodules('galaxyos.orchestration')
hiddenimports += collect_submodules('galaxyos.harness')
# Python 3.13 + PyInstaller 6.x frozen builds have a known issue
# where the bundled interpreter loses access to the host's
# libsqlite3 (or to a frozen copy of it). The fix is to declare
# `sqlite3` (and its C extension `_sqlite3`) explicitly. Without
# this, the sidecar crashes at first import that touches the DB
# layer (unified_coordinator / kora_behavior / dag_context_manager)
# with `No module named 'sqlite3'`. We add both the pure-Python
# wrapper and the C extension; collect_submodules handles the
# submodules like sqlite3.dbapi2.
hiddenimports += ['sqlite3', '_sqlite3']
# PyPI runtime deps the sidecar's Python code imports lazily (inside
# functions / via importlib). PyInstaller's static analysis misses
# these, so we declare them explicitly — `collect_submodules` then
# pulls in every subpackage, defending against runtime-only imports
# deep inside the dep tree (e.g. requests.adapters, openai._compat,
# tiktoken_ext). These calls are no-ops if the package isn't
# installed (returns []), so they're safe to leave even if a dep is
# dropped from the install list later.
for _pip_dep in (
    'requests', 'httpx', 'aiohttp', 'openai',
    'tiktoken', 'tiktoken_ext', 'ncps', 'jieba',
    'pydantic', 'pydantic_core', 'orjson',
    'psutil', 'pyzmq', 'zmq',
    'polars', 'duckdb',
    'numpy', 'scipy', 'sklearn',
    'PIL',  # pillow
):
    try:
        hiddenimports += collect_submodules(_pip_dep)
    except Exception:
        # Dep not installed in this build env — PyInstaller's
        # hooks may still discover the public surface, or the
        # dep is optional and the engine degrades gracefully.
        pass
# GalaxyOS engine has many legacy OpenClaw-style BARE imports inside
# galaxyos/engine/ (e.g. `from hallucination_guard import ...` instead
# of `from galaxyos.engine.hallucination_guard`). They expect the
# engine directory to be on sys.path so those bare names resolve to
# the *same* py files that ship as `galaxyos.engine.*`. PyInstaller
# already puts `_MEIPASS/galaxyos/` and `_MEIPASS/galaxyos/engine/`
# on sys.path via the `datas` entry below, so a frozen module doing
# `from hallucination_guard import ...` will resolve to
# `_MEIPASS/galaxyos/engine/hallucination_guard.py` — which is
# exactly the same file as `galaxyos.engine.hallucination_guard`.
# That's the whole reason `datas` must contain the WHOLE galaxyos/
# tree (not just the metadata files).

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
# Bundle `scripts/` (install_wizard.py + sibling scripts) so the
# desktop sidecar can spawn install_wizard as a subprocess for the
# "下载模型" UI button. install_wizard's path resolver detects the
# frozen layout (parent dir == 'scripts') and picks ~/.galaxyos as
# _OPENCLAW_HOME automatically — see scripts/install_wizard.py
# top-of-file _auto_detect_openclaw_home().
_scripts_dir = os.path.abspath(os.path.join(_spec_dir, '..', '..', 'scripts'))
assert os.path.isfile(os.path.join(_scripts_dir, 'install_wizard.py')), (
    f"scripts/install_wizard.py not found at {_scripts_dir}. "
    f"The desktop sidecar's /sse/install_wizard endpoint depends "
    f"on this file being present in the bundle."
)
datas += [
    (_scripts_dir, 'scripts'),
]
# Bundle `extensions/galaxyos/scripts/` so the desktop sidecar can spawn
# claw_worker.py (Python worker pool). IMPORTANT: do NOT bundle the
# entire extensions/galaxyos/ tree — the native/target/ dir alone is
# 700MB of Rust build artifacts. We only need the scripts/ subdir.
_ext_scripts_dir = os.path.abspath(os.path.join(_spec_dir, '..', '..', 'extensions', 'galaxyos', 'scripts'))
assert os.path.isdir(_ext_scripts_dir), (
    f"extensions/galaxyos/scripts/ not found at {_ext_scripts_dir}. "
    f"The desktop sidecar's claw_worker integration depends on this directory."
)
datas += [
    (_ext_scripts_dir, 'extensions/galaxyos/scripts'),
]
# Bundle repo-root skills/ (76 SKILL.md files) so the sidecar can
# scan them at runtime. PyInstaller's static analysis doesn't follow
# directory scans like `Path('skills').glob('**/SKILL.md')`, so
# we declare it explicitly. Without this, frozen builds crash on
# startup with FileNotFoundError: skills/.
# Use isdir guard so CI without a `skills/` (rare) doesn't fail.
_skills_dir = os.path.abspath(os.path.join(_spec_dir, '..', '..', 'skills'))
if os.path.isdir(_skills_dir):
    datas += [(_skills_dir, 'skills')]
else:
    print(f"[galaxyos-sidecar.spec] WARN: skills/ not found at {_skills_dir}; "
          f"skipping. Sidecar will boot with zero skills.")
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
# IMPORTANT: `pathex` deliberately does NOT include `'../../'`
# (the repo root) or `'../../galaxyos/engine'` / `'../../galaxyos/privileged'`.
# PyInstaller's static analysis, given those source paths, would
# treat the matching `*.py` files as *source-tree* modules and add
# them to the analysis TOC as bare names (`xiaoyi_memory`,
# `hallucination_guard`, etc.). But `datas` (above) ALSO copies the
# WHOLE `galaxyos/` tree into `_MEIPASS/galaxyos/`, which is the
# PACKAGED form (`galaxyos.engine.xiaoyi_memory`). At runtime the
# import system sees two entries that resolve to the *same physical
# file* and Python refuses with:
#     ImportError: cannot load module more than once per process
# (technically emitted by `_bootstrap._gcd_import` on the second
# `ModuleSpec` registration of the same loader path). This was the
# v0.1.1 root cause: sidecar started, loaded XiaoYiClawLLM cleanly,
# then crashed the moment it tried to import the next module via a
# bare `from <name> import ...` statement inside galaxyos/engine/.
# The `hiddenimports` list above already enumerates every engine
# submodule, so the static analyser doesn't need the source tree
# to discover them — it just needs the entry point and hidden
# imports.
a = Analysis(
    ['galaxyos_sidecar.py'],
    pathex=['.'],
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
