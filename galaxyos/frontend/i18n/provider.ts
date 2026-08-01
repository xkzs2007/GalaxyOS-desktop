import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import common_zh from './zh/common.json';
import common_en from './en/common.json';
import cognitive_zh from './zh/cognitive-panel.json';
import cognitive_en from './en/cognitive-panel.json';
import memory_zh from './zh/memory-panel.json';
import memory_en from './en/memory-panel.json';
import chat_zh from './zh/chat.json';
import chat_en from './en/chat.json';
import settings_zh from './zh/settings.json';
import settings_en from './en/settings.json';

const savedLocale = localStorage.getItem('galaxyos-locale') || 'zh';

i18n.use(initReactI18next).init({
  resources: {
    zh: {
      common: common_zh,
      'cognitive-panel': cognitive_zh,
      'memory-panel': memory_zh,
      chat: chat_zh,
      settings: settings_zh,
    },
    en: {
      common: common_en,
      'cognitive-panel': cognitive_en,
      'memory-panel': memory_en,
      chat: chat_en,
      settings: settings_en,
    },
  },
  lng: savedLocale,
  fallbackLng: 'zh',
  ns: ['common', 'cognitive-panel', 'memory-panel', 'chat', 'settings'],
  defaultNS: 'common',
  interpolation: { escapeValue: false },
});

export default i18n;