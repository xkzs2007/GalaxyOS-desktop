"""GalaxyOS init — 初始化引导"""

from __future__ import annotations

# ── lazy import map (PEP 562) — trimmed for upstream push ────────────────
_lazy = {
    "AUDIT_LOG_DIR": "galaxyos.init.init_path_resolver",
    "BootstrapContext": "galaxyos.init.bootstrap",
    "BootstrapReport": "galaxyos.init.bootstrap",
    "BootstrapWatchdog": "galaxyos.init.bootstrap",
    "CACHE_DIR": "galaxyos.init.init_path_resolver",
    "CORE_DIR": "galaxyos.init.init_path_resolver",
    "Capability": "galaxyos.init.bootstrap",
    "CapabilityResult": "galaxyos.init.bootstrap",
    "DAG_DB": "galaxyos.init.init_path_resolver",
    "DAG_DB_PATH": "galaxyos.init.init_path_resolver",
    "DATA_DIR": "galaxyos.init.init_path_resolver",
    "DEFAULT_BIND_IP": "galaxyos.init.deployment_profile",
    "DepCapability": "galaxyos.init.bootstrap",
    "DependencyChecker": "galaxyos.init.dep_checker",
    "DependencyInfo": "galaxyos.init.dep_checker",
    "ENGINE_DIR": "galaxyos.init.init_path_resolver",
    "EnvCapability": "galaxyos.init.bootstrap",
    "GALAXYOS_CAPABILITY": "galaxyos.init.init_path_resolver",
    "GALAXYOS_EXT_VAR": "galaxyos.init.init_path_resolver",
    "GalaxyBootstrap": "galaxyos.init.bootstrap",
    "LLM_CONFIG_JSON": "galaxyos.init.init_path_resolver",
    "LLM_MEMORY_CORE_DIR": "galaxyos.init.init_path_resolver",
    "LLM_MEMORY_DIR": "galaxyos.init.init_path_resolver",
    "LOGS_DIR": "galaxyos.init.init_path_resolver",
    "LOG_DIR": "galaxyos.init.init_path_resolver",
    "MEMORY_DIR": "galaxyos.init.init_path_resolver",
    "MEMORY_TDAI_DIR": "galaxyos.init.init_path_resolver",
    "MODELS_DIR": "galaxyos.init.init_path_resolver",
    "ModuleStatus": "galaxyos.init.dep_checker",
    "OPENCLAW_CONFIG": "galaxyos.init.init_path_resolver",
    "OPENCLAW_HOME": "galaxyos.init.init_path_resolver",
    "PRIVILEGED_DIR": "galaxyos.init.init_path_resolver",
    "RCI_SHARED_STATE": "galaxyos.init.init_path_resolver",
    "SKILLS_DIR": "galaxyos.init.init_path_resolver",
    "TDAI_CACHE_DIR": "galaxyos.init.init_path_resolver",
    "UpdateCapability": "galaxyos.init.bootstrap",
    "VECTORS_DB": "galaxyos.init.init_path_resolver",
    "VersionCapability": "galaxyos.init.bootstrap",
    "WORKSPACE_ROOT": "galaxyos.init.init_path_resolver",
    "XIAOYI_OMEGA_LLM_CONFIG": "galaxyos.init.init_path_resolver",
    "check_dependencies": "galaxyos.init.dep_checker",
    "checker": "galaxyos.init.dep_checker",
    "cli_main": "galaxyos.init.bootstrap",
    "detect_deploy_mode": "galaxyos.init.deployment_profile",
    "disable_stage": "galaxyos.init.progressive_setup",
    "enable_all": "galaxyos.init.progressive_setup",
    "enable_stage": "galaxyos.init.progressive_setup",
    "ensure_dir": "galaxyos.init.init_path_resolver",
    "ext_var_dir": "galaxyos.init.init_path_resolver",
    "find_native_lib": "galaxyos.init.init_path_resolver",
    "get_deploy_mode": "galaxyos.init.deployment_profile",
    "get_missing_dependencies": "galaxyos.init.dep_checker",
    "get_module_status": "galaxyos.init.dep_checker",
    "get_profile": "galaxyos.init.deployment_profile",
    "load_config": "galaxyos.init.progressive_setup",
    "load_profile": "galaxyos.init.deployment_profile",
    "logger": "galaxyos.init.bootstrap",
    "models_dir": "galaxyos.init.init_path_resolver",
    "native_lib_dirs": "galaxyos.init.init_path_resolver",
    "openclaw_path": "galaxyos.init.init_path_resolver",
    "path_resolver": "galaxyos.init.init_path_resolver",
    "print_dependency_report": "galaxyos.init.dep_checker",
    "repo_dir": "galaxyos.init.init_path_resolver",
    "save_config": "galaxyos.init.progressive_setup",
    "show_status": "galaxyos.init.progressive_setup",
    "var_dir": "galaxyos.init.init_path_resolver",
    "workspace": "galaxyos.init.init_path_resolver",
    "workspace_path": "galaxyos.init.init_path_resolver",
}


def __getattr__(name: str):
    """PEP 562 — 按需加载子模块符号，避免循环导入与启动开销。"""
    mod_path = _lazy.get(name)
    if mod_path is not None:
        import importlib
        mod = importlib.import_module(mod_path)
        val = getattr(mod, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_lazy.keys())
