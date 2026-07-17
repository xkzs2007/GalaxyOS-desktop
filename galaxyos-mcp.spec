from PyInstaller.utils.hooks import collect_all

fastmcp_datas, fastmcp_binaries, fastmcp_hiddenimports = collect_all('fastmcp')
mcp_datas, mcp_binaries, mcp_hiddenimports = collect_all('mcp')
starlette_datas, starlette_binaries, starlette_hiddenimports = collect_all('starlette')
uvicorn_datas, uvicorn_binaries, uvicorn_hiddenimports = collect_all('uvicorn')
openjiuwen_datas, openjiuwen_binaries, openjiuwen_hiddenimports = collect_all('openjiuwen')

a = Analysis(
    ['galaxyos/kernel/mcp_server_entry.py'],
    pathex=[],
    binaries=[
        *fastmcp_binaries,
        *mcp_binaries,
        *starlette_binaries,
        *uvicorn_binaries,
        *openjiuwen_binaries,
    ],
    datas=[
        ('galaxyos', 'galaxyos'),
        ('skills', 'skills'),
        *fastmcp_datas,
        *mcp_datas,
        *starlette_datas,
        *uvicorn_datas,
        *openjiuwen_datas,
    ],
    hiddenimports=[
        'galaxyos.kernel.mcp_server',
        'galaxyos.kernel.mcp_client',
        'galaxyos.kernel.liquid_memory_adapter',
        'galaxyos.kernel.dag_context_fusion',
        'galaxyos.kernel.memory_sync_bridge',
        'galaxyos.kernel.rccam_injector',
        'galaxyos.kernel.agent_core_bridge',
        'galaxyos.kernel.tokui_builder',
        'galaxyos.kernel.tokui_sse_streamer',
        'galaxyos.kernel.tokui_degradation',
        'galaxyos.kernel.skill_executor',
        'galaxyos.kernel.dual_runtime_manager',
        'galaxyos.kernel.llm_router_proxy',
        'galaxyos.kernel.swarm_agent_server_bridge',
        'galaxyos.kernel.swarm_hook_bridge',
        'galaxyos.kernel.galaxyos_extension',
        'galaxyos.kernel.workflow_hook_dispatcher',
        'galaxyos.kernel.skill_infra_direct_executor',
        'galaxyos.kernel.llm_router_direct',
        'galaxyos.shared.constants',
        'galaxyos.shared.paths',
        'galaxyos.shared.audit',
        'galaxyos.shared.fusion_guard',
        'galaxyos.harness.agent',
        'galaxyos.harness.workspace',
        'numpy',
        'scipy',
        'pydantic',
        'httpx',
        'aiohttp',
        'orjson',
        'openai',
        *fastmcp_hiddenimports,
        *mcp_hiddenimports,
        *starlette_hiddenimports,
        *uvicorn_hiddenimports,
        *openjiuwen_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'transformers', 'faiss', 'hnswlib', 'onnxruntime', 'pandas', 'tkinter', 'matplotlib', 'openjiuwen_studio'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='galaxyos-mcp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='galaxyos-mcp',
)
