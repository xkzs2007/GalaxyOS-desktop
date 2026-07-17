"""
I18nManager — GalaxyOS Python 后端国际化管理器

翻译资源加载、键查找、MCP 工具描述翻译、错误消息翻译、降级消息翻译。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class I18nManager:
    DEFAULT_LOCALE = "zh"
    SUPPORTED_LOCALES = ["zh", "en"]

    def __init__(self, translations_dir: str = ""):
        self._translations_dir = translations_dir or self._resolve_translations_dir()
        self._locale = self.DEFAULT_LOCALE
        self._translations: Dict[str, Dict[str, str]] = {}
        self._namespaces: Dict[str, Dict[str, Dict[str, str]]] = {}

    def _resolve_translations_dir(self) -> str:
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "translations"),
            os.path.join(".", "galaxyos", "translations"),
            os.path.join(".", "translations"),
        ]
        for c in candidates:
            if os.path.isdir(c):
                return c
        return candidates[0]

    def load(self, locale: str = "") -> None:
        target = locale or self._locale
        self._translations[target] = {}
        main_file = os.path.join(self._translations_dir, f"{target}.json")
        if os.path.isfile(main_file):
            try:
                with open(main_file, "r", encoding="utf-8") as f:
                    self._translations[target] = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load translations for {target}: {e}")

        ns_dir = os.path.join(self._translations_dir, "namespaces")
        if os.path.isdir(ns_dir):
            for fname in os.listdir(ns_dir):
                if fname.endswith(f".{target}.json"):
                    ns_name = fname.replace(f".{target}.json", "")
                    fpath = os.path.join(ns_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            if ns_name not in self._namespaces:
                                self._namespaces[ns_name] = {}
                            self._namespaces[ns_name][target] = json.load(f)
                    except (json.JSONDecodeError, OSError) as e:
                        logger.warning(f"Failed to load namespace {ns_name} for {target}: {e}")

    def translate(self, key: str, locale: str = "", **kwargs) -> str:
        target = locale or self._locale
        parts = key.split(".")
        if len(parts) >= 2:
            ns = parts[0]
            sub_key = ".".join(parts[1:])
            ns_translations = self._namespaces.get(ns, {}).get(target, {})
            if sub_key in ns_translations:
                text = ns_translations[sub_key]
                return text.format(**kwargs) if kwargs else text

        translations = self._translations.get(target, {})
        if key in translations:
            text = translations[key]
            return text.format(**kwargs) if kwargs else text

        fallback = self._translations.get(self.DEFAULT_LOCALE, {})
        if key in fallback:
            text = fallback[key]
            return text.format(**kwargs) if kwargs else text

        return key

    def set_locale(self, locale: str) -> None:
        if locale not in self.SUPPORTED_LOCALES:
            logger.warning(f"Unsupported locale: {locale}")
            return
        if locale not in self._translations:
            self.load(locale)
        self._locale = locale

    def get_locale(self) -> str:
        return self._locale

    def get_supported_locales(self) -> List[str]:
        return list(self.SUPPORTED_LOCALES)

    def register_translations(self, namespace: str, locale: str, translations: Dict[str, str]) -> None:
        if namespace not in self._namespaces:
            self._namespaces[namespace] = {}
        self._namespaces[namespace][locale] = translations

    def get_mcp_tool_description(self, tool_name: str, locale: str = "") -> str:
        return self.translate(f"mcp_tools.{tool_name}.description", locale=locale)

    def get_error_message(self, error_code: str, locale: str = "", **kwargs) -> str:
        return self.translate(f"errors.{error_code}", locale=locale, **kwargs)

    def get_degradation_message(self, scenario: str, locale: str = "", **kwargs) -> str:
        return self.translate(f"degradation.{scenario}", locale=locale, **kwargs)