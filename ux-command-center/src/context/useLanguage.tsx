/**
 * Thin shim over react-i18next (Program BIL / WS-0, ADR-028).
 *
 * The pre-i18next implementation was a bespoke React context holding a flat
 * `Record<Lang, Record<englishString, translated>>`. i18next now owns language state,
 * persistence and `<html lang>` (see `src/i18n/index.ts`); this module survives only to
 * keep the `{ lang, setLang, toggleLang, t }` surface its callers already use.
 *
 * `t` is still re-exported for compatibility — `Layout.tsx` destructures it — but it is now
 * i18next's `t`, bound to the `common` namespace. **New code should call
 * `useTranslation('<namespace>')` directly**; reach for `useLanguage()` only when you need
 * `lang` / `setLang` / `toggleLang`.
 */
import React from 'react';
import { I18nextProvider, useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import i18n, { normalizeLang, type Lang } from '../i18n';

export type { Lang };

interface LanguageContextType {
  lang: Lang;
  setLang: (l: Lang) => void;
  toggleLang: () => void;
  t: TFunction<'common'>;
}

/**
 * Retained so `App.tsx` and `test-utils.tsx` need no churn. react-i18next resolves the
 * default instance without a provider, but binding it explicitly keeps the tree honest
 * about where translations come from and guarantees `src/i18n` is imported (and therefore
 * initialized) wherever the app or a test mounts.
 */
export const LanguageProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
);

export function useLanguage(): LanguageContextType {
  const { t, i18n: instance } = useTranslation('common');
  const lang = normalizeLang(instance.resolvedLanguage ?? instance.language);

  const setLang = React.useCallback(
    (l: Lang) => {
      void instance.changeLanguage(normalizeLang(l));
    },
    [instance],
  );

  const toggleLang = React.useCallback(() => {
    void instance.changeLanguage(
      normalizeLang(instance.resolvedLanguage ?? instance.language) === 'en' ? 'zh-CN' : 'en',
    );
  }, [instance]);

  return { lang, setLang, toggleLang, t };
}
