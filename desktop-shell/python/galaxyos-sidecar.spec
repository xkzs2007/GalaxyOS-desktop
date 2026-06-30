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
datas = []
# GalaxyOS engine package (sits at ../../galaxyos relative to spec).
# Two entries cover the layout used in this repo:
#   desktop-shell/python/galaxyos-sidecar.spec
#   desktop-shell/python/   ← spec CWD
#   galaxyos/               ← target dir, two levels up
#
# `datas` entries use the platform-correct separator automatically
# (`: ` on POSIX, `;` on Windows) because the spec is parsed as
# Python source.
datas = []
datas += [
    ('../../galaxyos', 'galaxyos'),
]
# Pull in any package_data for sibling modules (safe even if empty).
for mod in sibling_modules:
    try:
        datas += collect_data_files(mod)
    except Exception:
        # Module not installed as a package; PyInstaller still picks
        # up the .py file via hiddenimports.
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
