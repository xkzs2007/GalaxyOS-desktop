"""
TokuiChatI18nAdapter — tokui_chat i18n 适配

通过 postMessage 注入翻译资源或 URL 参数传递 locale，
实现 tokui_chat 与 GalaxyOS 语言偏好同步。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class TokuiChatI18nAdapter:
    INJECT_TIMEOUT = 5.0

    def __init__(self, i18n_manager=None):
        self._i18n_manager = i18n_manager
        self._injected = False
        self._pending_locale: Optional[str] = None
        self._cached_translations: Optional[Dict[str, Any]] = None

    async def inject_translations(self) -> Dict[str, Any]:
        if not self._i18n_manager:
            return {"status": "skipped", "reason": "no i18n_manager"}

        locale = self._i18n_manager.get_locale()
        translations = self._build_translations_payload(locale)
        self._cached_translations = translations
        self._injected = True
        return {"status": "injected", "locale": locale}

    async def switch_locale(self, locale: str) -> Dict[str, Any]:
        if not self._i18n_manager:
            self._pending_locale = locale
            return {"status": "pending", "locale": locale}

        self._i18n_manager.set_locale(locale)
        translations = self._build_translations_payload(locale)
        self._cached_translations = translations
        return {"status": "switched", "locale": locale}

    def get_injection_status(self) -> Dict[str, Any]:
        return {
            "injected": self._injected,
            "pending_locale": self._pending_locale,
            "current_locale": self._i18n_manager.get_locale() if self._i18n_manager else None,
        }

    def _build_translations_payload(self, locale: str) -> Dict[str, Any]:
        payload = {"locale": locale}
        if self._i18n_manager:
            for ns in ["mcp_tools", "errors", "degradation", "common"]:
                ns_translations = self._i18n_manager._namespaces.get(ns, {}).get(locale, {})
                if ns_translations:
                    payload[ns] = ns_translations
        return payload

    def get_url_params(self, locale: str) -> str:
        return f"?locale={locale}"

    def get_post_message_payload(self, locale: str) -> str:
        payload = {
            "type": "galaxyos:i18n",
            "locale": locale,
            "translations": self._build_translations_payload(locale),
        }
        return json.dumps(payload, ensure_ascii=False)