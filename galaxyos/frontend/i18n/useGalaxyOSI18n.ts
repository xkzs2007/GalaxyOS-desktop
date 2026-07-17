import { useTranslation } from 'react-i18next';
import { useCallback } from 'react';

interface UseI18nReturn {
  t: ReturnType<typeof useTranslation>['0']['t'];
  locale: string;
  setLocale: (locale: string) => Promise<void>;
  loading: boolean;
}

export function useGalaxyOSI18n(): UseI18nReturn {
  const { t, i18n } = useTranslation();

  const setLocale = useCallback(async (locale: string) => {
    await i18n.changeLanguage(locale);
    localStorage.setItem('galaxyos-locale', locale);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('set_locale', { locale });
    } catch {
      console.warn('Tauri set_locale not available');
    }
  }, [i18n]);

  return {
    t,
    locale: i18n.language,
    setLocale,
    loading: !i18n.isInitialized,
  };
}