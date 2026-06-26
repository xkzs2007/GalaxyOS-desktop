"""
GalaxyOS Shared — 统一配置加载器

4层优先级链（高→低）:
  1. 环境变量  (GALAXYOS_* 前缀)
  2. 运行时覆盖  (set() / update())
  3. 配置文件  (llm_config.json / unified_config.json)
  4. 硬编码默认值

本模块不依赖 galaxyos.engine 或 galaxyos.privileged（仅 stdlib + galaxyos.shared.paths）。
"""

from __future__ import annotations
from galaxyos.shared.fusion_guard import fusion_replace

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, Union

from .paths import CONFIG_DIR, WORKSPACE

logger = logging.getLogger(__name__)


class ConfigLoader:
    """统一配置加载器 — 4层优先级链。

    Priority (high → low):
      1. Environment variables  (GALAXYOS_SECTION_KEY)
      2. Runtime overrides      (set / update)
      3. Config file            (JSON)
      4. Hardcoded defaults

    Usage:
        cfg = ConfigLoader()
        cfg.add_defaults({"llm": {"model": "LLM_GLM5", "temperature": 0.5}})
        cfg.load_file("llm_config.json")

        model = cfg.get("llm.model")          # 4层优先级
        cfg.set("llm.temperature", 0.7)       # 运行时覆盖
        temp = cfg.get("llm.temperature")     # 0.7

    Environment variable mapping:
        GALAXYOS_LLM_MODEL → llm.model
        GALAXYOS_LLM_BASE_URL → llm.base_url
        GALAXYOS_EMBEDDING_API_KEY → embedding.api_key
    """

    ENV_PREFIX = "GALAXYOS_"

    def __init__(self, config_dir: str | None = None) -> None:
        self._defaults: dict[str, Any] = {}
        self._file_config: dict[str, Any] = {}
        self._runtime: dict[str, Any] = {}
        self._config_dir = Path(config_dir) if config_dir else Path(CONFIG_DIR)
        self._loaded_files: list[str] = []

    # ── 层4: 默认值 ──

    def add_defaults(self, defaults: dict[str, Any]) -> None:
        """添加硬编码默认值（最低优先级）。"""
        self._defaults = _deep_merge(self._defaults, defaults)

    # ── 层3: 配置文件 ──

    def load_file(self, filename: str, *, required: bool = False) -> bool:
        """加载JSON配置文件。

        搜索路径:
          1. config_dir/filename
          2. WORKSPACE/filename
          3. ~/.galaxyos/filename

        Returns:
            True if file was loaded, False otherwise.
        """
        search_paths = [
            self._config_dir / filename,
            Path(WORKSPACE) / filename,
            Path(os.path.expanduser("~/.galaxyos")) / filename,
        ]

        for path in search_paths:
            if path.is_file():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._file_config = _deep_merge(self._file_config, data)
                    self._loaded_files.append(str(path))
                    return True
                except (json.JSONDecodeError, OSError):
                    if required:
                        raise
                    continue

        if required:
            raise FileNotFoundError(f"Config file not found: {filename} (searched: {search_paths})")
        return False

    # ── 层2: 运行时覆盖 ──

    def set(self, dotted_key: str, value: Any) -> None:
        """设置运行时覆盖值。dotted_key 如 'llm.model'。"""
        _set_nested(self._runtime, dotted_key, value)

    def update(self, overrides: dict[str, Any]) -> None:
        """批量设置运行时覆盖。"""
        for k, v in _flatten_dict(overrides).items():
            _set_nested(self._runtime, k, v)

    # ── 层1: 环境变量 ──

    def _env_lookup(self, dotted_key: str) -> tuple[bool, Any]:
        """检查环境变量 GALAXYOS_<SECTION>_<KEY>。"""
        env_key = self.ENV_PREFIX + dotted_key.upper().replace(".", "_")
        value = os.environ.get(env_key)
        if value is not None:
            # 尝试 JSON 解析（支持 bool/int/float/list/dict）
            try:
                return True, json.loads(value)
            except json.JSONDecodeError:
                return True, value
        return False, None

    # ── 读取 ──

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """按4层优先级链获取配置值。

        Priority: env > runtime > file > defaults
        """
        # 层1: 环境变量
        found, value = self._env_lookup(dotted_key)
        if found:
            return value

        # 层2: 运行时覆盖
        value = _get_nested(self._runtime, dotted_key)
        if value is not None:
            return value

        # 层3: 配置文件
        value = _get_nested(self._file_config, dotted_key)
        if value is not None:
            return value

        # 层4: 默认值
        value = _get_nested(self._defaults, dotted_key)
        if value is not None:
            return value

        return default

    def get_section(self, section: str) -> dict[str, Any]:
        """获取整个配置段（合并4层）。"""
        result = {}
        for source in (self._defaults, self._file_config, self._runtime):
            section_data = _get_nested(source, section)
            if isinstance(section_data, dict):
                result = _deep_merge(result, section_data)
        # 环境变量覆盖
        prefix = self.ENV_PREFIX + section.upper() + "_"
        for k, v in os.environ.items():
            if k.startswith(prefix) and k[len(prefix):]:
                sub_key = k[len(prefix):].lower()
                try:
                    result[sub_key] = json.loads(v)
                except json.JSONDecodeError:
                    result[sub_key] = v
        return result

    def require(self, dotted_key: str) -> Any:
        """获取必需配置，缺失时抛出 KeyError。"""
        value = self.get(dotted_key)
        if value is None:
            raise KeyError(f"Required config key missing: {dotted_key}")
        return value

    # ── 内省 ──

    @property
    def loaded_files(self) -> list[str]:
        """已加载的配置文件路径列表。"""
        return list(self._loaded_files)

    def as_dict(self) -> dict[str, Any]:
        """返回合并后的完整配置（不含环境变量覆盖）。"""
        result = {}
        for source in (self._defaults, self._file_config, self._runtime):
            result = _deep_merge(result, source)
        return result

    def __repr__(self) -> str:
        return (
            f"ConfigLoader(files={self._loaded_files}, "
            f"defaults={len(self._defaults)} keys, "
            f"runtime={len(self._runtime)} keys)"
        )


# ════════════════════════════════════════════════════════════════
# 全局单例
# ════════════════════════════════════════════════════════════════

_config: ConfigLoader | None = None


def get_config() -> ConfigLoader:
    """获取全局 ConfigLoader 单例。"""
    global _config
    if _config is None:
        _config = ConfigLoader()
    return _config


def reset_config() -> None:
    """重置全局单例（测试用）。"""
    global _config
    _config = None


# ════════════════════════════════════════════════════════════════
# 统一 load_config() — 合并所有散落变体
# ════════════════════════════════════════════════════════════════

@fusion_replace("galaxyos.init.progressive_setup", "load_config")
@fusion_replace("galaxyos.memory.backfill_l0_vectors", "load_config")
@fusion_replace("galaxyos.memory.hybrid_memory_search", "load_config")
@fusion_replace("galaxyos.reasoning.search", "load_config")
@fusion_replace("galaxyos.tools.bridge.llm_client", "load_config")
@fusion_replace("galaxyos.tools.bridge.llm_client_cli", "load_config")
@fusion_replace("galaxyos.tools.bridge.sqlite_ext", "load_config")
def load_config(
    config_path: Optional[Union[str, Path]] = None,
    defaults: Optional[dict[str, Any]] = None,
    field_aliases: Optional[dict[str, str]] = None,
    env_prefix: str = "",
    openclaw_json_path: Optional[Union[str, Path]] = None,
    *,
    log_errors: bool = True,
) -> dict[str, Any]:
    """统一配置加载函数 — 合并所有散落变体功能。

    合并了以下原散落实现的全部功能:
      - init/progressive_setup.py: 简单 JSON 文件读取
      - memory/backfill_l0_vectors.py: 带异常保护的 JSON 读取
      - memory/hybrid_memory_search.py: 带异常保护的 JSON 读取
      - memory/full_vector_recovery.py: JSON 文件读取
      - reasoning/search.py: 环境变量 + 配置文件 + openclaw.json 三层优先级
      - tools/bridge/llm_client.py: 带日志告警的 JSON 读取
      - tools/bridge/llm_client_cli.py: 带 print 告警的 JSON 读取
      - tools/bridge/sqlite_ext.py: 默认值合并 + 字段别名兼容

    Parameters
    ----------
    config_path : str | Path, optional
        配置文件路径。为 None 时自动搜索默认路径。
    defaults : dict, optional
        硬编码默认值，与文件配置深度合并（文件优先）。
    field_aliases : dict, optional
        字段别名映射，如 {"sqlite_vec_path": "vec0_path"}。
        旧字段值自动迁移到新字段。
    env_prefix : str
        环境变量前缀。为空时不从环境变量读取。
        reasoning/search.py 变体使用 EMBEDDING_API, LLM_BASE_URL 等。
    openclaw_json_path : str | Path, optional
        openclaw.json 路径（reasoning/search.py 变体的第二配置源）。
    log_errors : bool
        加载失败时是否记录日志（True 用 logger.warning，False 静默）。

    Returns
    -------
    dict[str, Any]
        合并后的配置字典。

    Examples
    --------
    >>> # 简单用法（向后兼容 progressive_setup / backfill 等变体）
    >>> config = load_config()
    >>> # 带默认值（向后兼容 sqlite_ext 变体）
    >>> config = load_config(defaults={"vec0_path": None})
    >>> # 带字段别名（向后兼容 sqlite_ext 的 sqlite_vec_path → vec0_path）
    >>> config = load_config(field_aliases={"sqlite_vec_path": "vec0_path"})
    >>> # 带环境变量（向后兼容 reasoning/search 变体）
    >>> config = load_config(env_prefix="EMBEDDING_")
    """
    result: dict[str, Any] = {}

    # ── 层4: 默认值 ──
    if defaults:
        result = _deep_merge(result, defaults)

    # ── 层3: 配置文件 ──
    if config_path is not None:
        config_file = Path(config_path)
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                result = _deep_merge(result, file_data)
            except Exception as e:
                if log_errors:
                    logger.warning(f"配置文件加载失败: {e}")
        else:
            if log_errors:
                logger.debug(f"配置文件不存在: {config_path}")

    # ── 字段别名迁移 ──
    if field_aliases:
        for old_key, new_key in field_aliases.items():
            if old_key in result and new_key not in result:
                result[new_key] = result.pop(old_key)

    # ── 层2: openclaw.json 第二配置源 ──
    if openclaw_json_path is not None:
        oc_file = Path(openclaw_json_path)
        if oc_file.exists():
            try:
                with open(oc_file, "r", encoding="utf-8") as f:
                    oc_data = json.load(f)
                # reasoning/search.py 变体: 从 plugins.entries.memory-tencentdb.config.embedding 读取
                emb = (
                    oc_data.get("plugins", {})
                    .get("entries", {})
                    .get("memory-tencentdb", {})
                    .get("config", {})
                    .get("embedding", {})
                )
                if emb:
                    embedding_section = result.setdefault("embedding", {})
                    if isinstance(embedding_section, dict):
                        if "baseUrl" in emb and "base_url" not in embedding_section:
                            embedding_section["base_url"] = emb["baseUrl"]
                        if "apiKey" in emb and "api_key" not in embedding_section:
                            embedding_section["api_key"] = emb["apiKey"]
            except Exception:
                logger.warning("load_config: Exception - <no detail>")

    # ── 层1: 环境变量 ──
    if env_prefix:
        # reasoning/search.py 变体的环境变量映射
        env_mappings = {
            "EMBEDDING_API": ("embedding", "base_url"),
            "EMBEDDING_API_KEY": ("embedding", "api_key"),
            "LLM_BASE_URL": ("llm", "base_url"),
            "LLM_API_KEY": ("llm", "api_key"),
            "LLM_UID": ("llm", "uid"),
        }
        for env_var, (section, key) in env_mappings.items():
            value = os.environ.get(env_var)
            if value:
                result.setdefault(section, {})[key] = value

    return result


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _get_nested(data: dict, dotted_key: str) -> Any:
    """通过点号路径获取嵌套字典值。"""
    keys = dotted_key.split(".")
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _set_nested(data: dict, dotted_key: str, value: Any) -> None:
    """通过点号路径设置嵌套字典值。"""
    keys = dotted_key.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典（override 优先）。"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _flatten_dict(data: dict, prefix: str = "") -> dict[str, Any]:
    """将嵌套字典展平为点号路径。"""
    result = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten_dict(v, key))
        else:
            result[key] = v
    return result
