"""
IntegrationConfigManager — GalaxyOS 集成配置管理器

提供配置驱动的开关和运行时配置变更通知，管理 EUI-NEO / tokui_chat / i18n 集成配置。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List

logger = logging.getLogger(__name__)


@dataclass
class IntegrationConfig:
    native_render_enabled: bool = False
    chat_frontend: str = "swarm"
    eui_render_backend: str = "auto"
    eui_window_backend: str = "auto"
    fallback_chain: List[str] = field(default_factory=lambda: ["eui_native", "webview_dom", "plain_text"])
    sse_enabled: bool = True
    cognitive_panel_visible: bool = True
    tokui_chat_path: str = ""
    eui_neo_lib_path: str = ""
    locale: str = "zh"
    supported_locales: List[str] = field(default_factory=lambda: ["zh", "en"])
    locale_detection: bool = True

    @classmethod
    def get_defaults(cls) -> IntegrationConfig:
        return cls()


class IntegrationConfigManager:
    def __init__(self, config_dir: str = ""):
        self._config_dir = config_dir or self._resolve_config_dir()
        self._config_path = os.path.join(self._config_dir, "integration_config.json")
        self._config: IntegrationConfig = IntegrationConfig.get_defaults()
        self._watchers: List[Callable[[str, Any], None]] = []
        self._last_loaded: float = 0

    def _resolve_config_dir(self) -> str:
        config_home = os.environ.get("GALAXYOS_HOME", "")
        if config_home:
            return os.path.join(config_home, "config")
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if hasattr(sys, "argv") else "."
        candidate = os.path.join(exe_dir, "config")
        if os.path.isdir(candidate):
            return candidate
        return os.path.join(".", "config")

    def load(self) -> IntegrationConfig:
        if not os.path.isfile(self._config_path):
            logger.info(f"Integration config not found at {self._config_path}, creating defaults")
            self._config = IntegrationConfig.get_defaults()
            self.save()
            return self._config

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            integrations = data.get("integrations", {})
            eui_neo = integrations.get("eui_neo", {})
            tokui_chat = integrations.get("tokui_chat", {})
            i18n = integrations.get("i18n", {})

            self._config = IntegrationConfig(
                native_render_enabled=eui_neo.get("native_render_enabled", False),
                chat_frontend=tokui_chat.get("chat_frontend", "swarm"),
                eui_render_backend=eui_neo.get("eui_render_backend", "auto"),
                eui_window_backend=eui_neo.get("eui_window_backend", "auto"),
                fallback_chain=eui_neo.get("fallback_chain", ["eui_native", "webview_dom", "plain_text"]),
                sse_enabled=tokui_chat.get("sse_enabled", True),
                cognitive_panel_visible=tokui_chat.get("cognitive_panel_visible", True),
                tokui_chat_path=tokui_chat.get("tokui_chat_path", ""),
                eui_neo_lib_path=eui_neo.get("eui_neo_lib_path", ""),
                locale=i18n.get("locale", "zh"),
                supported_locales=i18n.get("supported_locales", ["zh", "en"]),
                locale_detection=i18n.get("locale_detection", True),
            )
            self._last_loaded = time.time()
            logger.info(f"Integration config loaded from {self._config_path}")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Integration config corrupted: {e}, using defaults")
            self._config = IntegrationConfig.get_defaults()

        return self._config

    def save(self) -> None:
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        data = {
            "version": "1.0",
            "integrations": {
                "eui_neo": {
                    "native_render_enabled": self._config.native_render_enabled,
                    "eui_render_backend": self._config.eui_render_backend,
                    "eui_window_backend": self._config.eui_window_backend,
                    "fallback_chain": self._config.fallback_chain,
                    "eui_neo_lib_path": self._config.eui_neo_lib_path,
                },
                "tokui_chat": {
                    "chat_frontend": self._config.chat_frontend,
                    "sse_enabled": self._config.sse_enabled,
                    "cognitive_panel_visible": self._config.cognitive_panel_visible,
                    "tokui_chat_path": self._config.tokui_chat_path,
                },
                "i18n": {
                    "locale": self._config.locale,
                    "supported_locales": self._config.supported_locales,
                    "locale_detection": self._config.locale_detection,
                },
            },
        }
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get(self) -> IntegrationConfig:
        return self._config

    def set(self, key: str, value: Any) -> None:
        if not hasattr(self._config, key):
            logger.warning(f"Unknown config key: {key}")
            return
        old_value = getattr(self._config, key)
        if old_value == value:
            return
        setattr(self._config, key, value)
        self.save()
        for watcher in self._watchers:
            try:
                watcher(key, value)
            except Exception as e:
                logger.warning(f"Config watcher error for {key}: {e}")

    def watch(self, callback: Callable[[str, Any], None]) -> None:
        self._watchers.append(callback)

    @classmethod
    def get_defaults(cls) -> IntegrationConfig:
        return IntegrationConfig.get_defaults()
