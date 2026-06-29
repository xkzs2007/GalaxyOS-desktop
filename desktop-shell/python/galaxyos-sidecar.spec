# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['path_resolver_desktop', 'tokui_dsl', 'agent_loop', 'memo_stages', 'memo_adapter', 'executive_client', 'ac_router', 'tools']
hiddenimports += collect_submodules('galaxyos.engine')
hiddenimports += collect_submodules('galaxyos.privileged')
hiddenimports += collect_submodules('galaxyos.shared')


a = Analysis(
    ['galaxyos_sidecar.py'],
    pathex=['.', '../../'],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
