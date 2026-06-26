"""GalaxyOS Deployment Profile - 统一部署配置加载器"""
import os
try:
    import yaml
except ImportError:
    yaml = None
from pathlib import Path
import logging
logger = logging.getLogger(__name__)

_PROFILE_CACHE = None

# 默认绑定 IP（开发环境 localhost，生产环境通过 GALAXYOS_BIND_IP 环境变量覆盖）
DEFAULT_BIND_IP = os.environ.get("GALAXYOS_BIND_IP", "127.0.0.1")


def _is_container() -> bool:
    """检测是否运行在容器中"""
    # 方法1: /.dockerenv
    if os.path.exists("/.dockerenv"):
        return True
    # 方法2: /proc/1/cgroup
    try:
        with open("/proc/1/cgroup") as f:
            content = f.read()
            if "kubepods" in content or "docker" in content:
                return True
    except (FileNotFoundError, PermissionError):
        logger.warning("_is_container: (FileNotFoundError, PermissionError) - <no detail>")
    # 方法3: 环境变量
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    return False


def detect_deploy_mode() -> str:
    """自动检测部署模式"""
    # 1. 环境变量显式指定(最高优先级)
    explicit = os.environ.get("GALAXYOS_DEPLOY_MODE")
    if explicit in ("local", "cloud"):
        return explicit
    # 2. 自动检测
    if _is_container():
        return "cloud"
    return "local"


def load_profile(mode: str = None) -> dict:
    """加载部署配置Profile"""
    global _PROFILE_CACHE
    if _PROFILE_CACHE is not None and mode is None:
        return _PROFILE_CACHE

    if mode is None:
        mode = detect_deploy_mode()

    # 查找profile文件
    config_dir = Path(__file__).parent.parent / "config"
    profile_file = config_dir / f"profile_{mode}.yaml"

    if not profile_file.exists():
        # fallback: 内置默认值
        return _get_builtin_defaults(mode)

    if yaml is None:
        logger.warning("PyYAML not installed; using builtin defaults for profile")
        return _get_builtin_defaults(mode)

    with open(profile_file, "r", encoding="utf-8") as f:
        profile = yaml.safe_load(f)

    # 环境变量覆盖(最高优先级)
    profile = _apply_env_overrides(profile)

    _PROFILE_CACHE = profile
    return profile


def _apply_env_overrides(profile: dict) -> dict:
    """环境变量覆盖Profile配置"""
    env_mapping = {
        "GALAXYOS_HTTP_HOST": "http_host",
        "GALAXYOS_HTTP_PORT": ("http_port", int),
        "GALAXYOS_ZMQ_HOST": "zmq_host",
        "GALAXYOS_ZMQ_PORT": ("zmq_port", int),
        "GALAXYOS_VAR_DIR": "var_dir",
        "GALAXYOS_MODELS_DIR": "models_dir",
        "GALAXYOS_MEMORY_LIMIT_MB": ("memory_limit_mb", int),
        "GALAXYOS_LOG_LEVEL": "log_level",
        "GALAXYOS_WORKER_COUNT": ("worker_pool_size", int),
    }
    for env_key, mapping in env_mapping.items():
        env_val = os.environ.get(env_key)
        if env_val is not None:
            if isinstance(mapping, tuple):
                profile_key, converter = mapping
                profile[profile_key] = converter(env_val)
            else:
                profile[mapping] = env_val
    return profile


def _get_builtin_defaults(mode: str) -> dict:
    """内置默认值(YAML文件不存在时的fallback)"""
    if mode == "cloud":
        return {
            "deploy_mode": "cloud", "platform": "linux",
            "entry_point": "direct", "http_host": "0.0.0.0",
            "http_port": 8765, "zmq_host": "0.0.0.0", "zmq_port": 5555,
            "zmq_mode": "tcp", "var_dir": "/var/galaxyos",
            "models_dir": "/opt/galaxyos/models",
            "torch_variant": "cpu", "memory_limit_mb": 6144,
            "log_level": "INFO", "log_format": "json",
            "worker_pool_size": 4, "health_check_interval": 10,
        }
    else:  # local
        return {
            "deploy_mode": "local", "platform": "windows",
            "entry_point": "nodejs", "http_host": "127.0.0.1",
            "http_port": 8765, "zmq_host": DEFAULT_BIND_IP, "zmq_port": 5555,
            "zmq_mode": "tcp", "var_dir": "./var",
            "models_dir": "./models",
            "torch_variant": "cpu", "memory_limit_mb": 0,
            "log_level": "DEBUG", "log_format": "console",
            "worker_pool_size": 2, "health_check_interval": 30,
        }


def get_profile() -> dict:
    """获取当前Profile(懒加载)"""
    return load_profile()


def get_deploy_mode() -> str:
    """获取当前部署模式"""
    return get_profile().get("deploy_mode", "local")
